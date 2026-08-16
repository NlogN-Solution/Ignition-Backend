from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import AppointmentStatus, AppointmentType

if TYPE_CHECKING:
    from .lead import Lead
    from .user import User


class Appointment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "appointments"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    counsellor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Extra staff invited to the appointment alongside the primary counsellor
    # (e.g. a document specialist sitting in) — no per-row metadata needed,
    # so a plain array beats a join table here.
    attendee_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(PGUUID(as_uuid=True)))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    appointment_type: Mapped[AppointmentType] = mapped_column(
        enum_type(AppointmentType, "appointment_type", create_type=False),
        nullable=False,
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        enum_type(AppointmentStatus, "appointment_status", create_type=False),
        nullable=False,
        server_default=AppointmentStatus.SCHEDULED.value,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    preferred_date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    location: Mapped[str | None] = mapped_column(String(255))
    meeting_link: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    student: Mapped[User] = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="appointments_as_student",
    )
    counsellor: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[counsellor_id],
        back_populates="appointments_as_counsellor",
    )
    lead: Mapped[Lead | None] = relationship(back_populates="appointments")
    creator: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[created_by],
        back_populates="appointments_created",
    )

    __table_args__ = (
        Index("idx_appointments_student_id", "student_id"),
        Index("idx_appointments_counsellor_id", "counsellor_id"),
        Index("idx_appointments_lead_id", "lead_id"),
        Index("idx_appointments_status", "status"),
        Index("idx_appointments_start_time", "start_time"),
    )

    def __repr__(self) -> str:
        return f"<Appointment id={self.id} title={self.title}>"
