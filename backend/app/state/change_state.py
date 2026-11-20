from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_result import AgentResult
from app.schemas.finding import Finding
from app.schemas.review import MergeRecommendation, ReviewStatus


class ChangeState(BaseModel):
    """
    Shared state for one automated code review.

    Stores repository information, change data,
    individual agent outputs and final review results.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    # Review identification
    review_id: str = Field(default_factory=lambda: str(uuid4()))

    # Repository information
    repository: str = Field(min_length=1, description="Repository owner/name")

    pull_request_number: int | None = Field(default=None, gt=0)

    base_commit: str = Field(min_length=1)
    head_commit: str = Field(min_length=1)

    # Change information
    changed_files: list[str] = Field(default_factory=list)

    diff_content: str = ""

    change_summary: str = ""

    # Individual agent outputs
    agent_results: dict[str, AgentResult] = Field(default_factory=dict)

    # Consolidated review information
    final_findings: list[Finding] = Field(default_factory=list)

    recommendation: MergeRecommendation = MergeRecommendation.REVIEW_REQUIRED

    status: ReviewStatus = ReviewStatus.PENDING

    # Execution timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    completed_at: datetime | None = None

    @property
    def total_findings(self) -> int:
        return len(self.final_findings)

    @property
    def is_completed(self) -> bool:
        return self.status == ReviewStatus.COMPLETED
