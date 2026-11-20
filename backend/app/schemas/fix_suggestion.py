from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatchValidationStatus(str, Enum):
    UNVALIDATED = "UNVALIDATED"
    VALID = "VALID"
    INVALID = "INVALID"


class FixApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FixSuggestion(BaseModel):
    """A proposed code fix linked to a consolidated review finding."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    suggestion_id: str = Field(
        default_factory=lambda: str(uuid4()),
    )

    review_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)

    file_path: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1)

    unified_diff: str = Field(min_length=1)

    validation_status: PatchValidationStatus = PatchValidationStatus.UNVALIDATED

    approval_status: FixApprovalStatus = FixApprovalStatus.PENDING

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_approval(self):
        if (
            self.approval_status == FixApprovalStatus.APPROVED
            and self.validation_status != PatchValidationStatus.VALID
        ):
            raise ValueError(
                "A fix suggestion must have a valid patch before approval."
            )

        if (
            self.approval_status != FixApprovalStatus.PENDING
            and self.reviewed_at is None
        ):
            raise ValueError("Reviewed suggestions must have a reviewed_at timestamp.")

        return self
