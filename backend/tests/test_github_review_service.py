from app.schemas.finding import Finding, Severity
from app.services.github_review_service import (
    build_github_review_body,
    build_github_review_comments,
)


DIFF = """diff --git a/src/example.py b/src/example.py
index 1111111..2222222 100644
--- a/src/example.py
+++ b/src/example.py
@@ -1,2 +1,3 @@
 first = 1
+unsafe_call(user_input)
 second = 2
"""


def make_finding(**overrides):
    data = {
        "agent_name": "SecurityAgent",
        "title": "Unsafe input",
        "description": "User input reaches a sensitive operation.",
        "severity": Severity.HIGH,
        "confidence": 0.98,
        "file_path": "src/example.py",
        "start_line": 2,
        "end_line": 2,
        "evidence": "unsafe_call(user_input)",
        "recommendation": "Validate the input before use.",
    }
    data.update(overrides)
    return Finding(**data)


def test_finding_on_added_line_becomes_right_side_inline_comment():
    comments = build_github_review_comments([make_finding()], DIFF)
    assert len(comments) == 1
    assert comments[0]["path"] == "src/example.py"
    assert comments[0]["line"] == 2
    assert comments[0]["side"] == "RIGHT"
    assert "Unsafe input" in comments[0]["body"]


def test_finding_not_on_added_line_is_not_guessed_as_inline_comment():
    comments = build_github_review_comments(
        [make_finding(start_line=1, end_line=1)], DIFF
    )
    assert comments == []


def test_path_traversal_finding_is_never_published_inline():
    comments = build_github_review_comments(
        [make_finding(file_path="../secrets.txt")], DIFF
    )
    assert comments == []


def test_duplicate_findings_on_same_line_are_deduplicated():
    comments = build_github_review_comments(
        [make_finding(), make_finding()], DIFF
    )
    assert len(comments) == 1


def test_review_body_includes_summary_recommendation_and_severity_counts():
    from app.schemas.review import Review

    review = Review(
        repository="owner/repo",
        base_commit="a" * 40,
        head_commit="b" * 40,
        summary="Review summary",
        findings=[make_finding()],
    )
    body = build_github_review_body(review)
    assert "Review summary" in body
    assert "REVIEW_REQUIRED" in body
    assert "| HIGH | 1 |" in body
