from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import EmploymentEventType

if TYPE_CHECKING:
    from .user import EmployeeProfile, User


class Department(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    manager: Mapped[User | None] = relationship("User", foreign_keys=[manager_id])
    employee_profiles: Mapped[list[EmployeeProfile]] = relationship(
        back_populates="department_ref",
        foreign_keys="[EmployeeProfile.department_id]",
    )

    __table_args__ = (
        # ED360 scoped this to (organization_id, name); single-tenant it is just name.
        UniqueConstraint("name", name="uq_departments_name"),
        Index("idx_departments_manager_id", "manager_id"),
    )

    def __repr__(self) -> str:
        return f"<Department id={self.id} name={self.name}>"


class EmployeeEmploymentEvent(Base, UUIDPKMixin):
    """The employee lifecycle audit timeline — joined, promoted, department
    changed, etc. A domain-specific timeline table, same shape as LeadActivity
    and ApplicationStatusHistory elsewhere in this app, kept separate from the
    account/security-scoped ActivityLog on purpose."""

    __tablename__ = "employee_employment_events"

    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[EmploymentEventType] = mapped_column(
        enum_type(EmploymentEventType, "employment_event_type", create_type=False),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    employee_profile: Mapped[EmployeeProfile] = relationship(back_populates="employment_events")
    changed_by_user: Mapped[User | None] = relationship("User", foreign_keys=[changed_by])

    __table_args__ = (
        Index("idx_employee_employment_events_employee_profile_id", "employee_profile_id"),
        Index("idx_employee_employment_events_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<EmployeeEmploymentEvent id={self.id} event_type={self.event_type}>"
