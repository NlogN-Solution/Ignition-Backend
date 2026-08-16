from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import AttendanceSource, AttendanceStatus

if TYPE_CHECKING:
    from .user import User


class AttendancePolicy(Base, UUIDPKMixin, TimestampMixin):
    """The single work schedule check-ins are measured against.

    ED360 kept one row per organization and enforced that with a unique
    constraint on organization_id. Single-tenant there should be exactly one row
    ever, so `is_singleton` (always true, unique, CHECK-constrained) keeps that
    guarantee at the database level rather than trusting the service to
    get-or-create correctly.
    """

    __tablename__ = "attendance_policies"

    is_singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    work_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, server_default="{0,1,2,3,4}")
    expected_start_time: Mapped[time] = mapped_column(Time, nullable=False, server_default="09:00:00")
    expected_end_time: Mapped[time] = mapped_column(Time, nullable=False, server_default="17:00:00")
    grace_period_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10")
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")

    __table_args__ = (
        CheckConstraint("is_singleton IS TRUE", name="attendance_policies_singleton_flag"),
        UniqueConstraint("is_singleton", name="uq_attendance_policies_singleton"),
    )

    def __repr__(self) -> str:
        return f"<AttendancePolicy id={self.id}>"


class AttendanceRecord(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "attendance_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    check_out_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    worked_seconds: Mapped[int | None] = mapped_column(Integer)
    overtime_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[AttendanceStatus] = mapped_column(
        enum_type(AttendanceStatus, "attendance_status", create_type=False),
        nullable=False,
        server_default=AttendanceStatus.PRESENT.value,
    )
    source: Mapped[AttendanceSource] = mapped_column(
        enum_type(AttendanceSource, "attendance_source", create_type=False),
        nullable=False,
        server_default=AttendanceSource.WEB.value,
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    recorder: Mapped[User | None] = relationship("User", foreign_keys=[recorded_by])

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_attendance_records_user_date"),
        Index("idx_attendance_records_user_id", "user_id"),
        Index("idx_attendance_records_date", "date"),
        Index("idx_attendance_records_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<AttendanceRecord id={self.id} user_id={self.user_id} date={self.date}>"
