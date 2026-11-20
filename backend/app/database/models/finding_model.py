from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.database.models.agent_result_model import AgentResultModel
    from app.database.models.review_model import ReviewModel


class FindingModel(Base):
    __tablename__ = "findings"

    __table_args__ = (
        Index("ix_findings_review_id", "review_id"),
        Index("ix_findings_agent_result_id", "agent_result_id"),
        Index("ix_findings_severity", "severity"),
    )

    finding_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    source_finding_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )

    review_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("reviews.review_id", ondelete="CASCADE"),
        nullable=False,
    )

    agent_result_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agent_results.id", ondelete="CASCADE"),
        nullable=True,
    )

    is_final: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    agent_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    file_path: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )

    start_line: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    end_line: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    evidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    recommendation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    rule_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    is_blocking: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    review: Mapped[ReviewModel] = relationship(
        back_populates="findings",
    )

    agent_result: Mapped[AgentResultModel | None] = relationship(
        back_populates="findings",
    )
