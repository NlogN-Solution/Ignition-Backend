from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import TaskPriority, TaskStatus, TaskType


class TaskBase(BaseModel):
    title: str
    description: str | None = None
    task_type: TaskType
    priority: TaskPriority | None = TaskPriority.MEDIUM
    status: TaskStatus | None = TaskStatus.PENDING
    assigned_to: UUID
    assigned_by: UUID | None = None
    student_id: UUID | None = None
    lead_id: UUID | None = None
    application_id: UUID | None = None
    due_date: datetime | None = None
    completed_at: datetime | None = None
    remarks: str | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    """`assigned_by` is absent — it records who delegated the task and is
    stamped from the authenticated user."""

    title: str | None = None
    description: str | None = None
    task_type: TaskType | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    assigned_to: UUID | None = None
    student_id: UUID | None = None
    lead_id: UUID | None = None
    application_id: UUID | None = None
    due_date: datetime | None = None
    completed_at: datetime | None = None
    remarks: str | None = None


class TaskRead(TaskBase):
    id: UUID
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TaskList(BaseModel):
    items: list[TaskRead]
    total: int
    page: int
    limit: int
