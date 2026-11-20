
import asyncio
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


@dataclass(frozen=True)
class PatchValidationResult:
    valid: bool
    message: str
    checked_commit: str


class GitHubPatchValidator:
    """Check a proposed patch against an exact GitHub commit."""

    def __init__(self, timeout_seconds: int = 120):
        self.timeout_seconds = timeout_seconds

    async def validate(
        self,
        repository: str,
        head_commit: str,
        file_path: str,
        unified_diff: str,
    ) -> PatchValidationResult:
        """Run Git operations outside the event loop."""

        return await asyncio.to_thread(
            self._validate_sync,
            repository,
            head_commit,
            file_path,
            unified_diff,
        )

    def _validate_sync(
        self,
        repository: str,
        head_commit: str,
        file_path: str,
        unified_diff: str,
    ) -> PatchValidationResult:
        if not REPOSITORY_PATTERN.fullmatch(repository):
            return PatchValidationResult(
                False, "Repository must use owner/name format.", head_commit
            )

        if not COMMIT_PATTERN.fullmatch(head_commit):
            return PatchValidationResult(
                False, "The reviewed head commit must be a full Git SHA.", head_commit
            )

        try:
            safe_path = self._validate_relative_path(file_path)
            self._validate_diff_headers(unified_diff, safe_path)
        except ValueError as exc:
            return PatchValidationResult(False, str(exc), head_commit)

        clone_url = f"https://github.com/{repository}.git"

        try:
            with tempfile.TemporaryDirectory(prefix="acr-patch-") as temp_dir:
                checkout = Path(temp_dir) / "repository"

                self._run_git(
                    [
                        "clone",
                        "--quiet",
                        "--no-checkout",
                        "--filter=blob:none",
                        "--depth=1",
                        clone_url,
                        str(checkout),
                    ],
                    cwd=Path(temp_dir),
                )

                self._run_git(
                    ["fetch", "--quiet", "--depth=1", "origin", head_commit],
                    cwd=checkout,
                )

                self._run_git(
                    ["checkout", "--quiet", "--detach", "FETCH_HEAD"],
                    cwd=checkout,
                )

                target = (checkout / Path(*safe_path.parts)).resolve(strict=True)

                if not target.is_relative_to(checkout.resolve()):
                    raise ValueError("Target file resolves outside the checkout.")

                if not target.is_file():
                    raise ValueError("Target path is not a regular file.")

                self._run_git(
                    [
                        "apply",
                        "--check",
                        "--whitespace=error",
                        "--",
                    ],
                    cwd=checkout,
                    input_text=unified_diff,
                )

            return PatchValidationResult(
                True,
                "Patch applies cleanly to the reviewed GitHub commit. "
                "This confirms applicability, not semantic correctness.",
                head_commit,
            )

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "GitHub patch validation failed for %s at %s: %s",
                repository,
                head_commit,
                getattr(exc, "stderr", None) or str(exc),
            )
            return PatchValidationResult(
                False,
                "Git checkout or patch applicability check failed.",
                head_commit,
            )

        except (OSError, ValueError) as exc:
            logger.warning("Patch validation could not complete: %s", exc)
            return PatchValidationResult(False, str(exc), head_commit)

    @staticmethod
    def _validate_relative_path(file_path: str) -> PurePosixPath:
        if not file_path or "\x00" in file_path or "\\" in file_path:
            raise ValueError("Invalid patch file path.")

        path = PurePosixPath(file_path)

        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Patch path must be a safe relative repository path.")

        if path.parts and ":" in path.parts[0]:
            raise ValueError("Drive-qualified paths are not allowed.")

        return path

    @staticmethod
    def _validate_diff_headers(
        unified_diff: str,
        expected_path: PurePosixPath,
    ) -> None:
        if not unified_diff or not unified_diff.strip():
            raise ValueError("The unified diff is empty.")

        if "\x00" in unified_diff:
            raise ValueError("The unified diff contains a null byte.")

        lines = unified_diff.splitlines()
        old_header_indices = [
            index for index, line in enumerate(lines) if line.startswith("--- ")
        ]
        new_header_indices = [
            index for index, line in enumerate(lines) if line.startswith("+++ ")
        ]

        if len(old_header_indices) != 1 or len(new_header_indices) != 1:
            raise ValueError("Patch must contain exactly one old/new file header.")

        old_index = old_header_indices[0]
        new_index = new_header_indices[0]
        if new_index != old_index + 1:
            raise ValueError("Unified diff file headers must be adjacent and ordered.")

        expected_path_text = expected_path.as_posix()
        expected_old = f"a/{expected_path_text}"
        expected_new = f"b/{expected_path_text}"
        old_path = lines[old_index][4:].split("\t", 1)[0]
        new_path = lines[new_index][4:].split("\t", 1)[0]

        if old_path != expected_old or new_path != expected_new:
            raise ValueError(
                "Patch must modify exactly the finding's expected file."
            )

        # Accept Git's standard metadata preamble, but reject arbitrary text.
        metadata_patterns = (
            re.compile(r"^diff --git a/(.+) b/(.+)$"),
            re.compile(r"^index [0-9a-fA-F]+\.\.[0-9a-fA-F]+(?: [0-7]{6})?$"),
            re.compile(
                r"^(?:new file mode|deleted file mode|old mode|new mode) [0-7]{6}$"
            ),
            re.compile(r"^(?:similarity|dissimilarity) index \d+%$"),
            re.compile(r"^(?:rename|copy) (?:from|to) .+$"),
        )
        for line in lines[:old_index]:
            if not any(pattern.fullmatch(line) for pattern in metadata_patterns):
                raise ValueError("Patch contains unsupported Git metadata preamble.")
            if line.startswith("diff --git "):
                match = metadata_patterns[0].fullmatch(line)
                assert match is not None
                if (
                    match.group(1) != expected_path_text
                    or match.group(2) != expected_path_text
                ):
                    raise ValueError("Git metadata path does not match expected file.")

        if not re.search(
            r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@",
            unified_diff,
            re.M,
        ):
            raise ValueError("Patch has no complete unified diff hunk header.")

    def _run_git(
        self,
        arguments: list[str],
        cwd: Path,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=True,
            shell=False,
        )
