from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import LeaveStatus

if TYPE_CHECKING:
    from .user import User


class LeaveType(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "leave_types"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_days_per_year: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        # ED360 scoped this to (organization_id, name); single-tenant it is just name.
        UniqueConstraint("name", name="uq_leave_types_name"),
    )

    def __repr__(self) -> str:
        return f"<LeaveType id={self.id} name={self.name}>"


class LeaveRequest(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "leave_requests"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Snapshotted at creation from the work-day policy — deliberately not
    # recomputed later, so a subsequent policy change can't silently change
    # what an already-submitted request "costs" against the balance.
    requested_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text)
    attachment_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[LeaveStatus] = mapped_column(
        enum_type(LeaveStatus, "leave_status", create_type=False),
        nullable=False,
        server_default=LeaveStatus.PENDING.value,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    leave_type: Mapped[LeaveType] = relationship("LeaveType")
    reviewer: Mapped[User | None] = relationship("User", foreign_keys=[reviewed_by])

    __table_args__ = (
        Index("idx_leave_requests_user_id", "user_id"),
        Index("idx_leave_requests_leave_type_id", "leave_type_id"),
        Index("idx_leave_requests_status", "status"),
        Index("idx_leave_requests_start_date", "start_date"),
    )

    def __repr__(self) -> str:
        return f"<LeaveRequest id={self.id} user_id={self.user_id} status={self.status}>"
