from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class FixSuggestionModel(Base):
    """Persist proposed code fixes and their approval lifecycle."""

    __tablename__ = "fix_suggestions"

    __table_args__ = (
        Index("ix_fix_suggestions_review_id", "review_id"),
        Index("ix_fix_suggestions_finding_id", "finding_id"),
        Index("ix_fix_suggestions_approval_status", "approval_status"),
    )

    suggestion_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    review_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("reviews.review_id", ondelete="CASCADE"),
        nullable=False,
    )

    # This stores the original Pydantic finding ID, not the
    # database FindingModel primary key.
    finding_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )

    file_path: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    explanation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    unified_diff: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    validation_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="UNVALIDATED",
    )

    approval_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDING",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
