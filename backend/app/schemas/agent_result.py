from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.finding import Finding


class AgentStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    SKIPPED = "SKIPPED"


class AgentResult(BaseModel):
    """
    Standardized execution result returned by every agent.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    agent_name: str = Field(min_length=1, description="Name of the executing agent")

    status: AgentStatus

    summary: str = Field(default="", description="Short summary of agent execution")

    findings: list[Finding] = Field(default_factory=list)

    execution_time_ms: int = Field(default=0, ge=0)

    error: str | None = None

    @property
    def is_successful(self) -> bool:
        return self.status == AgentStatus.SUCCESS

    @property
    def finding_count(self) -> int:
        return len(self.findings)
