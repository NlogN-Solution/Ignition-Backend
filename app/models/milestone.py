"""Whether the student has seen the good news yet.

An offer arriving is the emotional high point of the whole journey, and the
portal should say so properly — once. This table is the "once".

It records **acknowledgement only**. It is not a second copy of the offer:
`applications.offer_received_date` plus the linked `offer_letter` document
remain the single source of truth for what the offer *is*, and nothing here
duplicates them. What the offer state cannot answer is "has this student
already had the confetti", and a celebration that fires on every dashboard
load for the rest of the application is not a celebration.

Keyed on (application, kind) rather than on the student: a second offer from a
second university is a second piece of news and deserves its own moment. The
paywall is what does not repeat (one fee, all offers) — the celebration is
per-milestone, which is the opposite way round and correct in both cases.

Named `ApplicationMilestone`, not `StudentMilestone`: that name and the
`student_milestones` table are already taken by `student_progress.py`, which
tracks a student's standing on the *journey* ladder (profile complete, first
application opened). Different question, different lifetime, and conflating
them would have meant one table answering "how far along are you" and "have
you seen the confetti" at once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import UUIDPKMixin
from ..db.types import enum_type

if TYPE_CHECKING:
    from .application import Application
    from .user import User


class MilestoneKind(str, Enum):
    OFFER_RECEIVED = "offer_received"
    CAS_RECEIVED = "cas_received"
    VISA_APPROVED = "visa_approved"


class ApplicationMilestone(Base, UUIDPKMixin):
    __tablename__ = "application_milestones"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[MilestoneKind] = mapped_column(
        enum_type(MilestoneKind, "milestone_kind", create_type=False),
        nullable=False,
    )
    #: When it happened, as recorded by staff — not when the row was written.
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    #: NULL until the student has been shown the celebration. The whole point
    #: of the table.
    seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    student: Mapped[User] = relationship("User")
    application: Mapped[Application] = relationship("Application")

    __table_args__ = (
        # One milestone of each kind per application. Staff correcting an offer
        # date must not mint a second celebration.
        UniqueConstraint("application_id", "kind", name="uq_application_milestones_application_id_kind"),
        Index("idx_application_milestones_student_unseen", "student_id", "seen_at"),
    )

    def __repr__(self) -> str:
        return f"<ApplicationMilestone id={self.id} kind={self.kind} seen={self.seen_at is not None}>"
