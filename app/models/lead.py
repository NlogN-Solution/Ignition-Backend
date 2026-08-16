from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import (
    ConversionSource,
    FollowUpMethod,
    FollowUpOutcome,
    LeadActivityType,
    LeadPriority,
    LeadSource,
    LeadStatus,
    LostReason,
)

if TYPE_CHECKING:
    from .appointment import Appointment
    from .task import Task
    from .user import User


class Lead(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "leads"

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(20), nullable=False)

    source: Mapped[LeadSource] = mapped_column(
        enum_type(LeadSource, "lead_source", create_type=False),
        nullable=False,
        server_default=LeadSource.WEBSITE.value,
    )
    status: Mapped[LeadStatus] = mapped_column(
        enum_type(LeadStatus, "lead_status", create_type=False),
        nullable=False,
        server_default=LeadStatus.NEW.value,
    )
    priority: Mapped[LeadPriority] = mapped_column(
        enum_type(LeadPriority, "lead_priority", create_type=False),
        nullable=False,
        server_default=LeadPriority.WARM.value,
    )

    interested_country: Mapped[str | None] = mapped_column(String(100))
    interested_course: Mapped[str | None] = mapped_column(String(255))
    preferred_intake: Mapped[str | None] = mapped_column(String(50))
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(50)))

    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    converted_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    qualified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    qualified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    converted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    converted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    conversion_source: Mapped[ConversionSource | None] = mapped_column(
        enum_type(ConversionSource, "conversion_source", create_type=False)
    )

    lost_reason: Mapped[LostReason | None] = mapped_column(enum_type(LostReason, "lost_reason", create_type=False))
    lost_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    remarks: Mapped[str | None] = mapped_column(Text)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    assignee: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[assigned_to],
        back_populates="leads_assigned",
    )
    converted_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[converted_user_id],
        back_populates="lead_converted_from",
    )
    qualifier: Mapped[User | None] = relationship("User", foreign_keys=[qualified_by])
    converter: Mapped[User | None] = relationship("User", foreign_keys=[converted_by])
    activities: Mapped[list[LeadActivity]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
    )
    follow_ups: Mapped[list[LeadFollowUp]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="LeadFollowUp.attempt_number",
    )
    appointments: Mapped[list[Appointment]] = relationship(back_populates="lead")
    tasks: Mapped[list[Task]] = relationship(back_populates="lead")

    __table_args__ = (
        CheckConstraint("length(trim(phone)) > 0", name="leads_phone_not_empty"),
        Index("idx_leads_phone", "phone"),
        Index("idx_leads_status", "status"),
        Index("idx_leads_priority", "priority"),
        Index("idx_leads_source", "source"),
        Index("idx_leads_assigned_to", "assigned_to"),
        Index("idx_leads_converted_user_id", "converted_user_id"),
        Index("idx_leads_next_follow_up_at", "next_follow_up_at"),
        Index("idx_leads_email_unique_not_null", "email", unique=True, postgresql_where=text("email IS NOT NULL")),
    )

    def __repr__(self) -> str:
        return f"<Lead id={self.id} phone={self.phone} status={self.status}>"


class LeadActivity(Base, UUIDPKMixin):
    __tablename__ = "lead_activities"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    activity_type: Mapped[LeadActivityType] = mapped_column(
        enum_type(LeadActivityType, "lead_activity_type", create_type=False),
        nullable=False,
    )
    performed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    title: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    old_status: Mapped[LeadStatus | None] = mapped_column(enum_type(LeadStatus, "lead_status", create_type=False))
    new_status: Mapped[LeadStatus | None] = mapped_column(enum_type(LeadStatus, "lead_status", create_type=False))
    due_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    lead: Mapped[Lead] = relationship(back_populates="activities")
    performer: Mapped[User | None] = relationship(
        "User",
        back_populates="lead_activities_performed",
    )

    __table_args__ = (
        Index("idx_lead_activities_lead_id", "lead_id"),
        Index("idx_lead_activities_performed_by", "performed_by"),
        Index("idx_lead_activities_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<LeadActivity id={self.id} type={self.activity_type}>"


class LeadFollowUp(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "lead_follow_ups"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    counsellor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    method: Mapped[FollowUpMethod] = mapped_column(
        enum_type(FollowUpMethod, "follow_up_method", create_type=False),
        nullable=False,
    )
    outcome: Mapped[FollowUpOutcome | None] = mapped_column(
        enum_type(FollowUpOutcome, "follow_up_outcome", create_type=False)
    )
    notes: Mapped[str | None] = mapped_column(Text)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    lead: Mapped[Lead] = relationship(back_populates="follow_ups")
    counsellor: Mapped[User | None] = relationship("User", foreign_keys=[counsellor_id])

    __table_args__ = (
        CheckConstraint("attempt_number >= 1 AND attempt_number <= 7", name="lead_follow_ups_attempt_number_range"),
        Index("idx_lead_follow_ups_lead_id", "lead_id"),
        Index("idx_lead_follow_ups_counsellor_id", "counsellor_id"),
        Index("idx_lead_follow_ups_scheduled_at", "scheduled_at"),
    )

    def __repr__(self) -> str:
        return f"<LeadFollowUp id={self.id} lead_id={self.lead_id} attempt={self.attempt_number}>"
