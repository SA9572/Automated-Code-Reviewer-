import pytest
from pydantic import ValidationError

from app.schemas.finding import Finding, Severity
from app.schemas.review import MergeRecommendation, Review, ReviewStatus


def create_finding(severity):

    return Finding(
        agent_name="SecurityAgent",
        title=f"{severity.value} issue",
        description="Test finding.",
        severity=severity,
        confidence=0.95,
        file_path="src/config.py",
        start_line=10,
        evidence="Example evidence",
        recommendation="Fix the issue.",
    )


def test_review_creation():

    review = Review(
        repository="example/project",
        pull_request_number=42,
        base_commit="abc123",
        head_commit="def456",
    )

    assert review.repository == "example/project"
    assert review.pull_request_number == 42
    assert review.status == ReviewStatus.PENDING
    assert review.recommendation == MergeRecommendation.REVIEW_REQUIRED
    assert review.total_findings == 0


def test_findings_by_severity():

    findings = [
        create_finding(Severity.CRITICAL),
        create_finding(Severity.HIGH),
        create_finding(Severity.HIGH),
        create_finding(Severity.LOW),
    ]

    review = Review(
        repository="example/project",
        base_commit="abc123",
        head_commit="def456",
        findings=findings,
    )

    counts = review.findings_by_severity

    assert counts["CRITICAL"] == 1
    assert counts["HIGH"] == 2
    assert counts["LOW"] == 1
    assert counts["MEDIUM"] == 0
    assert counts["INFO"] == 0


def test_invalid_pull_request_number():

    with pytest.raises(ValidationError):
        Review(
            repository="example/project",
            pull_request_number=0,
            base_commit="abc123",
            head_commit="def456",
        )


def test_review_completion_status():

    review = Review(
        repository="example/project",
        base_commit="abc123",
        head_commit="def456",
        status=ReviewStatus.COMPLETED,
        recommendation=MergeRecommendation.REQUEST_CHANGES,
    )

    assert review.is_completed is True
    assert review.recommendation == MergeRecommendation.REQUEST_CHANGES


def test_unique_review_ids():

    first = Review(
        repository="example/project", base_commit="abc123", head_commit="def456"
    )

    second = Review(
        repository="example/project", base_commit="abc123", head_commit="def456"
    )

    assert first.review_id != second.review_id
