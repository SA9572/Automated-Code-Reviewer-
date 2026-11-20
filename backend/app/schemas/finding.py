from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class Finding(BaseModel):
    """
    Represents one issue discovered during code review.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    finding_id: str = Field(default_factory=lambda: str(uuid4()))

    agent_name: str = Field(
        min_length=1,
        description="Agent that discovered this issue",
    )

    title: str = Field(min_length=3, max_length=200)

    description: str = Field(min_length=1)

    severity: Severity

    confidence: float = Field(ge=0.0, le=1.0)

    file_path: str = Field(min_length=1)

    start_line: int | None = Field(default=None, ge=1)

    end_line: int | None = Field(default=None, ge=1)

    evidence: str = Field(min_length=1)

    recommendation: str = Field(min_length=1)

    rule_id: str | None = None

    @model_validator(mode="after")
    def validate_line_range(self):
        """
        Ensure the end line is not before the start line.

        Either line number may be omitted when the exact
        location is unknown.
        """

        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be greater than or equal to start_line")

        return self

    @property
    def is_blocking(self) -> bool:
        return self.severity in {Severity.CRITICAL, Severity.HIGH}
