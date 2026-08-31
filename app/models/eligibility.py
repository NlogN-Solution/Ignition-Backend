"""The public eligibility assessment (CATALOGUE-CMS-PLAN.md is silent on this;
see the feature brief).

**This is not a second CRM.** Everything about working the lead — status,
priority, who owns it, internal notes, follow-up attempts, conversion — already
exists on `Lead` and its `LeadActivity` / `LeadFollowUp` children, and this
table deliberately holds none of it. What it holds is the *submission*: the
answers a student gave, and what the server made of them.

The split matters because the two have different lifetimes. A submission is a
historical fact — it is what someone said on a Tuesday in August, and it must
never be edited to match a later conversation. The lead is a living record that
a counsellor changes all day. Putting a status column here would immediately
raise the question of which status is the real one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import TIMESTAMP, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import EligibilityIndicator, EligibilityOverall

if TYPE_CHECKING:
    from .lead import Lead
    from .user import User


class EligibilityAssessment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "eligibility_assessments"

    #: Every submission has a lead. That is the whole point of the feature —
    #: an assessment nobody can follow up on is a form that wasted a student's
    #: three minutes.
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Set when a signed-in student submits, or when the lead is later
    #: converted. Nullable because the form is public and asking someone to
    #: register before they can find out whether they qualify is backwards.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # --- The answers -----------------------------------------------------------
    # Five JSONB blobs rather than forty columns. The questions are editorial:
    # they will be reworded, reordered and extended by whoever owns the funnel,
    # and none of them is queried on — staff filter by the *assessment*, which
    # is typed below, and read the answers only once they have opened one
    # submission. A migration per question would be the wrong trade.
    education: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    english: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    course: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    finance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    documents: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: Denormalised out of `course` so the staff list can show and filter on it
    #: without reading a JSONB column for every row.
    study_level: Mapped[str | None] = mapped_column(String(50))
    preferred_course: Mapped[str | None] = mapped_column(String(255))
    preferred_location: Mapped[str | None] = mapped_column(String(100))
    #: University slugs, resolved against the shared catalogue on read.
    preferred_universities: Mapped[list[str] | None] = mapped_column(JSONB)

    # --- What the server made of them -----------------------------------------
    academic_status: Mapped[EligibilityIndicator] = mapped_column(
        enum_type(EligibilityIndicator, "eligibility_indicator", create_type=False),
        nullable=False,
    )
    english_status: Mapped[EligibilityIndicator] = mapped_column(
        enum_type(EligibilityIndicator, "eligibility_indicator", create_type=False),
        nullable=False,
    )
    financial_status: Mapped[EligibilityIndicator] = mapped_column(
        enum_type(EligibilityIndicator, "eligibility_indicator", create_type=False),
        nullable=False,
    )
    document_status: Mapped[EligibilityIndicator] = mapped_column(
        enum_type(EligibilityIndicator, "eligibility_indicator", create_type=False),
        nullable=False,
    )
    overall_status: Mapped[EligibilityOverall] = mapped_column(
        enum_type(EligibilityOverall, "eligibility_overall", create_type=False),
        nullable=False,
    )
    #: 0–100, from the document checklist. Explicitly *document* readiness and
    #: not a probability of anything — the copy on both sides says so.
    document_readiness: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Why the server said what it said, in the words shown to staff. Stored
    #: rather than recomputed so a submission read a year from now explains
    #: itself under the rules that were in force when it was made.
    assessment_notes: Mapped[list[str] | None] = mapped_column(JSONB)

    #: The ruleset that produced the four indicators above. Rules will change;
    #: historical assessments must not silently change with them.
    assessment_version: Mapped[str] = mapped_column(String(10), nullable=False, server_default="v1")

    # --- Provenance ------------------------------------------------------------
    #: Which page or campaign it came from.
    source_page: Mapped[str | None] = mapped_column(String(255))
    #: What the student typed in the free-text box, if anything.
    message: Mapped[str | None] = mapped_column(Text)
    #: The contact consent, recorded as given — a boolean plus when.
    consent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="eligibility_assessments")
    user: Mapped[User | None] = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_eligibility_assessments_lead_id", "lead_id"),
        Index("idx_eligibility_assessments_user_id", "user_id"),
        Index("idx_eligibility_assessments_overall_status", "overall_status"),
        Index("idx_eligibility_assessments_submitted_at", "submitted_at"),
    )

    def __repr__(self) -> str:
        return f"<EligibilityAssessment id={self.id} overall={self.overall_status}>"
