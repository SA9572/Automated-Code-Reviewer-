from pathlib import PurePosixPath

import pytest

from app.services.github_patch_validator import GitHubPatchValidator


VALID_DIFF = """--- a/app/example.py
+++ b/app/example.py
@@ -1,1 +1,1 @@
-old_value = 1
+new_value = 2
"""


def test_plain_unified_diff_headers_are_accepted():
    GitHubPatchValidator._validate_diff_headers(
        VALID_DIFF,
        PurePosixPath("app/example.py"),
    )


def test_git_metadata_preamble_is_accepted():
    diff = """diff --git a/app/example.py b/app/example.py
index 1111111..2222222 100644
--- a/app/example.py
+++ b/app/example.py
@@ -1,1 +1,1 @@
-old_value = 1
+new_value = 2
"""
    GitHubPatchValidator._validate_diff_headers(
        diff,
        PurePosixPath("app/example.py"),
    )


@pytest.mark.parametrize(
    "metadata",
    [
        "diff --git a/app/other.py b/app/other.py",
        "untrusted preamble",
    ],
)
def test_invalid_or_mismatched_git_metadata_is_rejected(metadata):
    diff = f"""{metadata}
--- a/app/example.py
+++ b/app/example.py
@@ -1,1 +1,1 @@
-old_value = 1
+new_value = 2
"""
    with pytest.raises(ValueError):
        GitHubPatchValidator._validate_diff_headers(
            diff,
            PurePosixPath("app/example.py"),
        )


def test_patch_with_wrong_target_file_is_rejected():
    with pytest.raises(ValueError, match="expected file"):
        GitHubPatchValidator._validate_diff_headers(
            VALID_DIFF,
            PurePosixPath("app/other.py"),
        )
