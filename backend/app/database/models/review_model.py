from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.database.models.agent_result_model import AgentResultModel
    from app.database.models.finding_model import FindingModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReviewModel(Base):
    __tablename__ = "reviews"

    __table_args__ = (
        Index("ix_reviews_repository", "repository"),
        Index("ix_reviews_status", "status"),
    )

    review_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    repository: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    pull_request_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    base_commit: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    head_commit: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Original change information
    changed_files: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    diff_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    # Review summary and status
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDING",
    )

    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    recommendation: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    agent_results: Mapped[list[AgentResultModel]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    findings: Mapped[list[FindingModel]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
