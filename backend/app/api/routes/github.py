
"""GitHub repository and pull-request review endpoints."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_github_client, get_review_pipeline
from app.api.schemas import (
    GitHubAuthStatusResponse,
    GitHubReviewSubmitRequest,
    GitHubReviewSubmitResponse,
)
from app.orchestration.review_pipeline import ReviewPipeline
from app.schemas.review import Review
from app.services.github_client import (
    GitHubAPIError,
    GitHubClient,
    GitHubNotConfiguredError,
    GitHubRateLimitError,
)
from app.services.github_review_service import (
    build_github_review_body,
    build_github_review_comments,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["GitHub"])

_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


def _validate_repository_parts(owner: str, repo: str) -> None:
    if (
        not _REPOSITORY_PART.fullmatch(owner)
        or not _REPOSITORY_PART.fullmatch(repo)
    ):
        raise HTTPException(
            status_code=400,
            detail="Repository owner and name must be valid GitHub path segments.",
        )


def _is_test_file(path: str) -> bool:
    """Identify common Python and JavaScript test-file conventions."""
    normalized = path.replace("\\", "/").casefold()
    filename = normalized.rsplit("/", 1)[-1]

    return (
        "/tests/" in f"/{normalized}"
        or filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename.endswith(".test.js")
        or filename.endswith(".test.jsx")
        or filename.endswith(".test.ts")
        or filename.endswith(".test.tsx")
        or filename.endswith(".spec.js")
        or filename.endswith(".spec.ts")
    )


def _build_test_diff(
    files: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Build test context only from actual GitHub PR file patches."""
    test_files: list[str] = []
    test_patches: list[str] = []

    for item in files:
        if not isinstance(item, dict):
            continue

        filename = item.get("filename")
        patch = item.get("patch")

        if not isinstance(filename, str) or not _is_test_file(filename):
            continue

        test_files.append(filename)

        if isinstance(patch, str) and patch.strip():
            test_patches.append(
                f"diff --git a/{filename} b/{filename}\n"
                f"--- a/{filename}\n"
                f"+++ b/{filename}\n"
                f"{patch}"
            )

    return "\n".join(test_patches), test_files


def _raise_github_http_error(exc: GitHubAPIError) -> None:
    if isinstance(exc, GitHubRateLimitError):
        detail: dict[str, Any] = {
            "message": exc.message,
            "retry_after_seconds": exc.retry_after,
        }
        raise HTTPException(status_code=429, detail=detail) from exc

    status_code = exc.status_code
    if status_code not in {400, 401, 403, 404, 409, 413, 422, 502, 503}:
        status_code = 502

    raise HTTPException(
        status_code=status_code,
        detail=exc.message,
    ) from exc


def _require_configured(client: GitHubClient) -> None:
    if not client.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub integration is not configured on the backend.",
        )


@router.get(
    "/auth/status",
    response_model=GitHubAuthStatusResponse,
)
async def github_auth_status(
    client: GitHubClient = Depends(get_github_client),
) -> GitHubAuthStatusResponse:
    """Expose whether GitHub is configured, never the token itself."""
    return GitHubAuthStatusResponse(configured=client.is_configured)


@router.get("/user")
async def get_github_user(
    client: GitHubClient = Depends(get_github_client),
) -> dict[str, Any]:
    _require_configured(client)

    try:
        return await client.get_authenticated_user()
    except GitHubNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GitHubAPIError as exc:
        _raise_github_http_error(exc)


@router.get("/repos")
async def list_github_repositories(
    per_page: int = 100,
    page: int = 1,
    client: GitHubClient = Depends(get_github_client),
) -> list[dict[str, Any]]:
    """List repositories visible to the configured GitHub identity."""
    if not 1 <= per_page <= 100 or page < 1:
        raise HTTPException(
            status_code=422,
            detail="Invalid pagination values.",
        )

    _require_configured(client)

    try:
        repositories = await client.list_repositories(
            per_page=per_page,
            page=page,
        )

        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "full_name": item.get("full_name"),
                "private": item.get("private"),
                "description": item.get("description"),
                "default_branch": item.get("default_branch"),
                "html_url": item.get("html_url"),
                "permissions": item.get("permissions"),
            }
            for item in repositories
        ]

    except GitHubAPIError as exc:
        _raise_github_http_error(exc)


@router.get("/repos/{owner}/{repo}/pulls")
async def list_github_pull_requests(
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 100,
    page: int = 1,
    client: GitHubClient = Depends(get_github_client),
) -> list[dict[str, Any]]:
    _validate_repository_parts(owner, repo)

    if (
        state not in {"open", "closed", "all"}
        or not 1 <= per_page <= 100
        or page < 1
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid pull-request filters.",
        )

    _require_configured(client)

    try:
        pulls = await client.list_pull_requests(
            owner,
            repo,
            state=state,
            per_page=per_page,
            page=page,
        )

        return [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "draft": item.get("draft"),
                "html_url": item.get("html_url"),
                "user": (item.get("user") or {}).get("login"),
                "head_sha": (item.get("head") or {}).get("sha"),
                "base_ref": (item.get("base") or {}).get("ref"),
                "updated_at": item.get("updated_at"),
            }
            for item in pulls
        ]

    except GitHubAPIError as exc:
        _raise_github_http_error(exc)


# Baseline reviews inspect source files at one repository commit.
# Keep this allowlist narrow so databases, binaries, vendored dependencies,
# and generated assets are not sent to the LLM.
_BASELINE_SOURCE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".php", ".rb", ".swift", ".kt", ".kts", ".sh",
    ".yaml", ".yml", ".toml", ".ini", ".cfg",
}

_BASELINE_EXCLUDED_PARTS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "coverage",
    ".pytest_cache",
    "vendor",
}

_BASELINE_MAX_FILE_BYTES = 512_000


def _is_baseline_source_file(path: str) -> bool:
    """Return whether a Git tree path is eligible for baseline review."""
    parsed = PurePosixPath(path)

    if (
        parsed.is_absolute()
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        return False

    if any(
        part.casefold() in _BASELINE_EXCLUDED_PARTS
        for part in parsed.parts
    ):
        return False

    if parsed.name.casefold() in {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
    }:
        return False

    return parsed.suffix.casefold() in _BASELINE_SOURCE_SUFFIXES


@router.post(
    "/repos/{owner}/{repo}/baseline-review",
    response_model=Review,
    summary="Run an AI baseline review against a GitHub repository",
)
async def run_github_baseline_review(
    owner: str,
    repo: str,
    client: GitHubClient = Depends(get_github_client),
    pipeline: ReviewPipeline = Depends(get_review_pipeline),
) -> Review:
    """
    Review actual source files from one complete default-branch snapshot.

    Unlike PR reviews, baseline reviews do not construct synthetic Git diffs.
    The source content is passed separately to the review agents.
    """
    _validate_repository_parts(owner, repo)
    _require_configured(client)

    try:
        metadata = await client.get_repository(owner, repo)

        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise HTTPException(
                status_code=502,
                detail="GitHub repository did not provide a default branch.",
            )

        commit = await client.get_branch_commit(
            owner,
            repo,
            default_branch,
        )

        if not isinstance(commit, dict):
            raise HTTPException(
                status_code=502,
                detail="GitHub returned invalid branch commit metadata.",
            )

        commit_sha = commit.get("sha")
        commit_info = commit.get("commit") or {}
        tree_info = commit_info.get("tree") or {}
        tree_sha = tree_info.get("sha")

        if not isinstance(commit_sha, str) or not isinstance(tree_sha, str):
            raise HTTPException(
                status_code=502,
                detail="GitHub did not return a valid commit and tree.",
            )

        tree_response = await client.get_git_tree(
            owner,
            repo,
            tree_sha,
        )

        if not isinstance(tree_response, dict):
            raise HTTPException(
                status_code=502,
                detail="GitHub returned an invalid repository tree response.",
            )

        if tree_response.get("truncated"):
            raise HTTPException(
                status_code=413,
                detail="Repository tree is too large for a complete baseline review.",
            )

        tree_entries = tree_response.get("tree")
        if not isinstance(tree_entries, list):
            raise HTTPException(
                status_code=502,
                detail="GitHub returned an invalid repository tree.",
            )

        source_entries = [
            entry
            for entry in tree_entries
            if (
                isinstance(entry, dict)
                and entry.get("type") == "blob"
                and isinstance(entry.get("path"), str)
                and isinstance(entry.get("sha"), str)
                and _is_baseline_source_file(entry["path"])
            )
        ]

        max_files = client.settings.GITHUB_MAX_FILES_PER_PR

        if len(source_entries) > max_files:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Repository contains more than the configured "
                    f"{max_files} reviewable source files."
                ),
            )

        if not source_entries:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No supported source files were found on the "
                    "repository default branch."
                ),
            )

        total_bytes = 0
        source_files: list[dict[str, str]] = []
        test_source_files: list[dict[str, str]] = []
        changed_files: list[str] = []
        test_files: list[str] = []

        for entry in sorted(
            source_entries,
            key=lambda item: item["path"],
        ):
            path = entry["path"]
            size = entry.get("size")

            if (
                isinstance(size, int)
                and size > _BASELINE_MAX_FILE_BYTES
            ):
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Source file exceeds the per-file review limit: "
                        f"{path}"
                    ),
                )

            blob = await client.get_git_blob(
                owner,
                repo,
                entry["sha"],
            )

            encoded = blob.get("content")
            if (
                not isinstance(encoded, str)
                or blob.get("encoding") != "base64"
            ):
                raise HTTPException(
                    status_code=502,
                    detail=f"GitHub returned invalid blob content for {path}.",
                )

            try:
                raw_content = base64.b64decode(
                    encoded,
                    validate=False,
                )
                content = raw_content.decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "A file selected for baseline review is not valid "
                        f"UTF-8 source text: {path}"
                    ),
                ) from exc

            total_bytes += len(raw_content)
            if total_bytes > client.settings.GITHUB_MAX_DIFF_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Repository source exceeds the configured "
                        "total review size limit."
                    ),
                )

            source_item = {
                "path": path,
                "content": content,
            }
            source_files.append(source_item)
            changed_files.append(path)

            if _is_test_file(path):
                test_source_files.append(source_item)
                test_files.append(path)

        if not source_files:
            raise HTTPException(
                status_code=422,
                detail="No UTF-8 source files were available for baseline review.",
            )

    except HTTPException:
        raise
    except GitHubNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    except GitHubAPIError as exc:
        _raise_github_http_error(exc)

    try:
        return await pipeline.run(
            repository=f"{owner}/{repo}",
            pull_request_number=None,
            base_commit=commit_sha,
            head_commit=commit_sha,
            diff_content="",
            changed_files=changed_files,
            test_diff="",
            test_files=test_files,
            review_mode="baseline",
            source_files=source_files,
            test_source_files=test_source_files,
            snapshot_complete=True,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Baseline AI review failed for GitHub repository %s/%s",
            owner,
            repo,
        )
        raise HTTPException(
            status_code=500,
            detail="The baseline repository review could not be completed.",
        ) from exc


@router.post(
    "/repos/{owner}/{repo}/pulls/{pull_number}/review",
    response_model=Review,
    summary="Run the AI review pipeline against a GitHub pull request",
)
async def run_github_pull_request_review(
    owner: str,
    repo: str,
    pull_number: int,
    client: GitHubClient = Depends(get_github_client),
    pipeline: ReviewPipeline = Depends(get_review_pipeline),
) -> Review:
    """Fetch PR metadata/diff and pass it to the existing review pipeline."""
    _validate_repository_parts(owner, repo)

    if pull_number < 1:
        raise HTTPException(
            status_code=422,
            detail="pull_number must be positive.",
        )

    _require_configured(client)

    try:
        pull = await client.get_pull_request(
            owner,
            repo,
            pull_number,
        )

        expected_repository = f"{owner}/{repo}".casefold()
        base_info = pull.get("base") or {}
        base_repo = base_info.get("repo") or {}
        actual_repository = str(
            base_repo.get("full_name", "")
        ).casefold()

        if actual_repository != expected_repository:
            raise HTTPException(
                status_code=409,
                detail="GitHub returned a pull request for a different repository.",
            )

        if pull.get("state") != "open":
            raise HTTPException(
                status_code=409,
                detail="Only open pull requests can be reviewed.",
            )

        base_sha = (pull.get("base") or {}).get("sha")
        head_sha = (pull.get("head") or {}).get("sha")

        if not isinstance(base_sha, str) or not isinstance(head_sha, str):
            raise HTTPException(
                status_code=502,
                detail="GitHub pull request is missing base/head commit SHAs.",
            )

        # Retrieve all changed-file metadata from GitHub.
        files = await client.list_pull_request_files(
            owner,
            repo,
            pull_number,
        )

        changed_files = [
            item["filename"]
            for item in files
            if (
                isinstance(item, dict)
                and isinstance(item.get("filename"), str)
            )
        ]

        # Extract actual test-file patches for TestCoverageAgent.
        test_diff, test_files = _build_test_diff(files)

        # Retrieve the complete PR diff for the other review agents.
        diff_content = await client.get_pull_request_diff(
            owner,
            repo,
            pull_number,
        )

        settings_limit = client.settings.GITHUB_MAX_DIFF_BYTES

        if len(diff_content.encode("utf-8")) > settings_limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Pull request diff exceeds configured limit "
                    f"({settings_limit} bytes)."
                ),
            )

        if not diff_content.strip():
            raise HTTPException(
                status_code=422,
                detail="GitHub returned an empty pull-request diff.",
            )

    except HTTPException:
        raise
    except GitHubNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    except GitHubAPIError as exc:
        _raise_github_http_error(exc)

    try:
        return await pipeline.run(
            repository=f"{owner}/{repo}",
            pull_request_number=pull_number,
            base_commit=base_sha,
            head_commit=head_sha,
            diff_content=diff_content,
            changed_files=changed_files,
            test_diff=test_diff,
            test_files=test_files,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "AI review failed for GitHub pull request %s/%s#%s",
            owner,
            repo,
            pull_number,
        )

        raise HTTPException(
            status_code=500,
            detail="The AI review could not be completed for this pull request.",
        ) from exc


@router.post(
    "/reviews/{review_id}/submit",
    response_model=GitHubReviewSubmitResponse,
    summary="Publish an existing AI review to GitHub",
)
async def submit_github_review(
    review_id: str,
    payload: GitHubReviewSubmitRequest,
    client: GitHubClient = Depends(get_github_client),
    pipeline: ReviewPipeline = Depends(get_review_pipeline),
) -> GitHubReviewSubmitResponse:
    """Publish review comments only after an explicit submit request."""
    _require_configured(client)

    try:
        state = await pipeline.state_manager.get_state(review_id)

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Review not found.",
        ) from exc

    if state.pull_request_number is None:
        raise HTTPException(
            status_code=400,
            detail="This review is not linked to a pull request.",
        )

    if state.status.value != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="Only completed reviews can be submitted.",
        )

    repository_parts = state.repository.split("/", maxsplit=1)

    if (
        len(repository_parts) != 2
        or not all(
            _REPOSITORY_PART.fullmatch(part)
            for part in repository_parts
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="Review contains an invalid repository identifier.",
        )

    owner, repo = repository_parts

    try:
        pull = await client.get_pull_request(
            owner,
            repo,
            state.pull_request_number,
        )

        latest_head = (pull.get("head") or {}).get("sha")
        base_info = pull.get("base") or {}
        base_repo = base_info.get("repo") or {}

        if (
            str(base_repo.get("full_name", "")).casefold()
            != state.repository.casefold()
        ):
            raise HTTPException(
                status_code=409,
                detail="The pull request repository no longer matches the stored review.",
            )

        if pull.get("state") != "open":
            raise HTTPException(
                status_code=409,
                detail="Only open pull requests can receive reviews.",
            )

        if latest_head != state.head_commit:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The pull request head changed after this review. "
                    "Run a fresh review before submitting."
                ),
            )

        diff_content = await client.get_pull_request_diff(
            owner,
            repo,
            state.pull_request_number,
        )

        if (
            len(diff_content.encode("utf-8"))
            > client.settings.GITHUB_MAX_DIFF_BYTES
        ):
            raise HTTPException(
                status_code=413,
                detail="Current pull-request diff exceeds the configured size limit.",
            )

        comments = build_github_review_comments(
            state.final_findings,
            diff_content,
        )[:50]

        from app.schemas.review import Review as ReviewSchema

        review = ReviewSchema(
            review_id=state.review_id,
            repository=state.repository,
            pull_request_number=state.pull_request_number,
            base_commit=state.base_commit,
            head_commit=state.head_commit,
            status=state.status,
            agent_results=state.agent_results,
            findings=state.final_findings,
            summary=state.change_summary,
            recommendation=state.recommendation,
            created_at=state.created_at,
            completed_at=state.completed_at,
        )

        body = build_github_review_body(review)

        result = await client.create_pull_request_review(
            owner,
            repo,
            state.pull_request_number,
            commit_id=state.head_commit,
            body=body,
            event=payload.event.value,
            comments=comments,
        )

        return GitHubReviewSubmitResponse(
            review_id=result["id"],
            state=result.get("state", payload.event.value),
            html_url=result.get("html_url"),
            comments_posted=len(comments),
            commit_id=state.head_commit,
        )

    except HTTPException:
        raise
    except GitHubNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    except GitHubAPIError as exc:
        _raise_github_http_error(exc)
