from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.finding import Finding
from app.schemas.review import Review


class ReviewCreateRequest(BaseModel):
    """Input payload for starting a code review."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    repository: str = Field(
        min_length=1,
        description="Repository owner/name",
    )

    base_commit: str = Field(min_length=1)
    head_commit: str = Field(min_length=1)

    diff_content: str = Field(
        description="Unified Git diff to analyze",
    )

    changed_files: list[str] = Field(default_factory=list)

    pull_request_number: int | None = Field(
        default=None,
        gt=0,
    )

    test_diff: str | None = None
    test_files: list[str] = Field(default_factory=list)


class ReviewListResponse(BaseModel):
    """Paginated response containing code review results."""

    items: list[Review]
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1)
    has_more: bool


class FindingListResponse(BaseModel):
    """Paginated response containing consolidated findings."""

    items: list[Finding]
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1)
    has_more: bool


class GitHubReviewEvent(str, Enum):
    """GitHub pull-request review event."""

    COMMENT = "COMMENT"
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class GitHubReviewSubmitRequest(BaseModel):
    """Explicit user action to publish an existing AI review to GitHub."""

    model_config = ConfigDict(extra="forbid")
    event: GitHubReviewEvent = GitHubReviewEvent.COMMENT


class GitHubReviewSubmitResponse(BaseModel):
    review_id: int
    state: str
    html_url: str | None = None
    comments_posted: int
    commit_id: str


class GitHubAuthStatusResponse(BaseModel):
    configured: bool
    provider: str = "GitHub REST API"
