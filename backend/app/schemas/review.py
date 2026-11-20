from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_result import AgentResult
from app.schemas.finding import Finding, Severity


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class MergeRecommendation(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Review(BaseModel):
    """
    Consolidated code review produced by Reviewer Manager.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    review_id: str = Field(default_factory=lambda: str(uuid4()))

    repository: str = Field(min_length=1, description="Repository owner/name")

    pull_request_number: int | None = Field(default=None, gt=0)

    base_commit: str = Field(min_length=1)
    head_commit: str = Field(min_length=1)

    status: ReviewStatus = ReviewStatus.PENDING

    agent_results: dict[str, AgentResult] = Field(default_factory=dict)

    findings: list[Finding] = Field(default_factory=list)

    summary: str = ""

    recommendation: MergeRecommendation = MergeRecommendation.REVIEW_REQUIRED

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    completed_at: datetime | None = None

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def findings_by_severity(self) -> dict[str, int]:

        counts = {severity.value: 0 for severity in Severity}

        for finding in self.findings:
            counts[finding.severity.value] += 1

        return counts

    @property
    def is_completed(self) -> bool:
        return self.status == ReviewStatus.COMPLETED
