from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import TaskPriority, TaskStatus, TaskType

if TYPE_CHECKING:
    from .application import Application
    from .lead import Lead
    from .user import User


class Task(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[TaskType] = mapped_column(
        enum_type(TaskType, "task_type", create_type=False),
        nullable=False,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        enum_type(TaskPriority, "task_priority", create_type=False),
        nullable=False,
        server_default=TaskPriority.MEDIUM.value,
    )
    status: Mapped[TaskStatus] = mapped_column(
        enum_type(TaskStatus, "task_status", create_type=False),
        nullable=False,
        server_default=TaskStatus.PENDING.value,
    )

    assigned_to: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    due_date: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    remarks: Mapped[str | None] = mapped_column(Text)

    assigned_to_user: Mapped[User] = relationship(
        "User",
        foreign_keys=[assigned_to],
        back_populates="tasks_assigned_to_me",
    )
    assigned_by_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[assigned_by],
        back_populates="tasks_assigned_by_me",
    )
    student_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="tasks_for_student",
    )
    lead: Mapped[Lead | None] = relationship(back_populates="tasks")
    application: Mapped[Application | None] = relationship(back_populates="tasks")

    __table_args__ = (
        Index("idx_tasks_assigned_to", "assigned_to"),
        Index("idx_tasks_assigned_by", "assigned_by"),
        Index("idx_tasks_student_id", "student_id"),
        Index("idx_tasks_lead_id", "lead_id"),
        Index("idx_tasks_application_id", "application_id"),
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_due_date", "due_date"),
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id} title={self.title}>"
