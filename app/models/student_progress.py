"""Progress milestones and the points ledger.

Phase 6, first two modules. Both advance from events rather than from client
calls — a student cannot POST themselves to 100% or award themselves points,
which is the whole reason the ledger is append-only and the award path runs in a
subscriber.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class ProgressMilestone(Base, UUIDPKMixin, TimestampMixin):
    """The journey every student is measured against.

    A catalog row, not per-student: the ladder is the same for everyone, and
    `StudentMilestone` records where a given student stands on it. Weights are
    stored rather than derived so the completion percentage can be re-weighted
    without a migration.
    """

    __tablename__ = "progress_milestones"

    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10")
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    def __repr__(self) -> str:
        return f"<ProgressMilestone key={self.key}>"


class StudentMilestone(Base, UUIDPKMixin, TimestampMixin):
    """One student's standing on one milestone."""

    __tablename__ = "student_milestones"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("progress_milestones.id", ondelete="CASCADE"),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    #: What completed it — an event name, or "manual" when staff marked it.
    source: Mapped[str | None] = mapped_column(String(60))

    student: Mapped[User] = relationship(back_populates="milestones")
    milestone: Mapped[ProgressMilestone] = relationship()

    __table_args__ = (
        UniqueConstraint("student_id", "milestone_id", name="uq_student_milestones_student_id_milestone_id"),
        Index("idx_student_milestones_student_id", "student_id"),
    )

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def __repr__(self) -> str:
        return f"<StudentMilestone student_id={self.student_id} complete={self.is_complete}>"


class PointsRule(Base, UUIDPKMixin, TimestampMixin):
    """How many points an action is worth.

    A table rather than a constant so the values can be tuned without a deploy,
    and so a ledger entry can name the rule that produced it.
    """

    __tablename__ = "points_rules"

    action: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    category: Mapped[str | None] = mapped_column(String(50))
    #: When true the rule may only ever fire once per student — "profile
    #: completed" is an achievement, "document uploaded" is repeatable.
    once_per_student: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    def __repr__(self) -> str:
        return f"<PointsRule action={self.action} points={self.points}>"


class StudentPointsLedger(Base, UUIDPKMixin):
    """Append-only record of every award.

    A ledger rather than a running total on `users`: the balance is a `SUM` over
    rows, so it can always be explained ("why do I have 140 points?") and can
    never drift from its own history. Nothing updates or deletes these rows.
    """

    __tablename__ = "student_points_ledger"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("points_rules.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: The row that caused the award, so an award can be traced to its document
    #: or application and a repeat for the same object suppressed.
    reference_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    student: Mapped[User] = relationship(back_populates="points_entries")
    rule: Mapped[PointsRule | None] = relationship()

    __table_args__ = (
        Index("idx_student_points_ledger_student_id", "student_id"),
        Index("idx_student_points_ledger_action", "action"),
        # One award per (student, action, source row): re-approving the same
        # document must not pay twice.
        UniqueConstraint(
            "student_id", "action", "reference_id", name="uq_student_points_ledger_student_action_reference"
        ),
    )

    def __repr__(self) -> str:
        return f"<StudentPointsLedger student_id={self.student_id} action={self.action} points={self.points}>"
