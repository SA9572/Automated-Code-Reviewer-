
"""Validate LLM-reported finding locations against review input."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

_HUNK_HEADER = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@"
)


def _normalize_path(path: str) -> str:
    """Normalize Git/Python path separators for reliable comparisons."""
    normalized = path.strip().replace("\\", "/")

    if normalized.startswith("./"):
        normalized = normalized[2:]

    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]

    return str(PurePosixPath(normalized))


def _parse_header_path(line: str) -> str | None:
    """Extract the new-side path from a unified diff +++ header."""
    value = line[4:].split("\t", maxsplit=1)[0].strip()

    if value == "/dev/null":
        return None

    if value.startswith("b/"):
        value = value[2:]

    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
        value = (
            value.replace(r'\"', '"')
            .replace(r"\\", "\\")
            .replace(r"\t", "\t")
            .replace(r"\n", "\n")
        )

    return _normalize_path(value) if value else None


def build_changed_line_map(
    diff_contents: str | Iterable[str],
) -> dict[str, set[int]]:
    """
    Return added line numbers keyed by each file's new-side path.

    Only '+' lines inside a valid unified-diff hunk are considered changed.
    Context lines advance the new-side cursor but are not marked as changed.
    Removed lines do not advance the new-side cursor.
    """
    diffs = (
        [diff_contents]
        if isinstance(diff_contents, str)
        else list(diff_contents)
    )

    changed_lines: dict[str, set[int]] = {}

    for diff_text in diffs:
        if not isinstance(diff_text, str) or not diff_text:
            continue

        current_path: str | None = None
        new_line_number: int | None = None

        for line in diff_text.splitlines():
            if line.startswith("+++ "):
                current_path = _parse_header_path(line)
                new_line_number = None
                continue

            hunk_match = _HUNK_HEADER.match(line)

            if hunk_match:
                if current_path is None:
                    new_line_number = None
                    continue

                new_start = int(hunk_match.group(1))
                new_count = hunk_match.group(2)

                if new_count is not None and int(new_count) == 0:
                    new_line_number = None
                else:
                    new_line_number = new_start

                continue

            if new_line_number is None or current_path is None:
                continue

            if line.startswith("\\"):
                continue

            if line.startswith("+") and not line.startswith("+++"):
                changed_lines.setdefault(current_path, set()).add(
                    new_line_number
                )
                new_line_number += 1

            elif line.startswith("-") and not line.startswith("---"):
                continue

            elif line.startswith(" "):
                new_line_number += 1

    return changed_lines


def validate_finding_location(
    finding_data: dict[str, Any],
    diff_contents: str | Iterable[str],
) -> dict[str, Any]:
    """
    Validate locations against added lines in a unified Git diff.

    This remains the validator for pull-request reviews.
    """
    validated = finding_data.copy()

    file_path = validated.get("file_path")

    if not isinstance(file_path, str) or not file_path.strip():
        validated["start_line"] = None
        validated["end_line"] = None
        return validated

    changed_line_map = build_changed_line_map(diff_contents)
    normalized_path = _normalize_path(file_path)
    changed_lines = changed_line_map.get(normalized_path, set())

    start_line = validated.get("start_line")
    end_line = validated.get("end_line")

    if start_line is None and end_line is None:
        return validated

    if start_line is None:
        if end_line not in changed_lines:
            validated["end_line"] = None
        return validated

    if end_line is None:
        if start_line not in changed_lines:
            validated["start_line"] = None
        return validated

    if (
        isinstance(start_line, bool)
        or not isinstance(start_line, int)
        or isinstance(end_line, bool)
        or not isinstance(end_line, int)
        or start_line < 1
        or end_line < start_line
    ):
        validated["start_line"] = None
        validated["end_line"] = None
        return validated

    matching_lines = [
        line_number
        for line_number in changed_lines
        if start_line <= line_number <= end_line
    ]

    if not matching_lines:
        validated["start_line"] = None
        validated["end_line"] = None
        return validated

    validated["start_line"] = min(matching_lines)
    validated["end_line"] = max(matching_lines)

    return validated


def build_source_line_map(
    source_files: Iterable[dict[str, Any]],
) -> dict[str, int]:
    """
    Return the actual line count for each supplied baseline source file.

    Expected input:
        [{"path": "app.py", "content": "line 1\\nline 2"}]
    """
    line_counts: dict[str, int] = {}

    for source_file in source_files:
        if not isinstance(source_file, dict):
            continue

        path = source_file.get("path")
        content = source_file.get("content")

        if not isinstance(path, str) or not path.strip():
            continue

        if not isinstance(content, str):
            continue

        normalized_path = _normalize_path(path)

        if not normalized_path or normalized_path == ".":
            continue

        line_counts[normalized_path] = len(content.splitlines())

    return line_counts


def validate_finding_location_in_source_files(
    finding_data: dict[str, Any],
    source_files: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """
    Validate a finding against actual baseline source-file line counts.

    Unlike PR validation, baseline reviews may report any existing source
    line, not only added lines. Invalid or unverifiable locations are cleared.
    """
    validated = finding_data.copy()

    file_path = validated.get("file_path")

    if not isinstance(file_path, str) or not file_path.strip():
        validated["start_line"] = None
        validated["end_line"] = None
        return validated

    line_counts = build_source_line_map(source_files)
    normalized_path = _normalize_path(file_path)
    total_lines = line_counts.get(normalized_path)

    if total_lines is None:
        validated["start_line"] = None
        validated["end_line"] = None
        return validated

    start_line = validated.get("start_line")
    end_line = validated.get("end_line")

    if start_line is None and end_line is None:
        return validated

    if start_line is not None and (
        isinstance(start_line, bool)
        or not isinstance(start_line, int)
        or not 1 <= start_line <= total_lines
    ):
        validated["start_line"] = None
        validated["end_line"] = None
        return validated

    if end_line is not None and (
        isinstance(end_line, bool)
        or not isinstance(end_line, int)
        or not 1 <= end_line <= total_lines
    ):
        validated["start_line"] = None
        validated["end_line"] = None
        return validated

    if (
        start_line is not None
        and end_line is not None
        and end_line < start_line
    ):
        validated["start_line"] = None
        validated["end_line"] = None

    return validated
