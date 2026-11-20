from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.database.models.finding_model import FindingModel
    from app.database.models.review_model import ReviewModel


class AgentResultModel(Base):
    __tablename__ = "agent_results"

    __table_args__ = (
        UniqueConstraint(
            "review_id",
            "agent_name",
            name="uq_agent_results_review_agent",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    review_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("reviews.review_id", ondelete="CASCADE"),
        nullable=False,
    )

    agent_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    execution_time_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    review: Mapped[ReviewModel] = relationship(
        back_populates="agent_results",
    )

    findings: Mapped[list[FindingModel]] = relationship(
        back_populates="agent_result",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
