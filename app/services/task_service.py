from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..core.events import TaskAssigned, event_bus
from ..models import Task
from .partial_update import reject_null_on_required


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_task(self, task_id: UUID) -> Task | None:
        return await self.session.scalar(select(Task).where(Task.id == task_id))

    async def list_tasks(
        self,
        page: int,
        limit: int,
        assigned_to: UUID | None = None,
        assigned_by: UUID | None = None,
        student_id: UUID | None = None,
        lead_id: UUID | None = None,
        application_id: UUID | None = None,
        status: str | None = None,
        priority: str | None = None,
        task_type: str | None = None,
        search: str | None = None,
    ) -> tuple[list[Task], int]:
        conditions: list[ColumnElement[bool]] = []
        if assigned_to:
            conditions.append(Task.assigned_to == assigned_to)
        if assigned_by:
            conditions.append(Task.assigned_by == assigned_by)
        if student_id:
            conditions.append(Task.student_id == student_id)
        if lead_id:
            conditions.append(Task.lead_id == lead_id)
        if application_id:
            conditions.append(Task.application_id == application_id)
        if status:
            conditions.append(Task.status == status)
        if priority:
            conditions.append(Task.priority == priority)
        if task_type:
            conditions.append(Task.task_type == task_type)
        if search and search.strip():
            value = f"%{search.strip().lower()}%"
            conditions.append(or_(func.lower(Task.title).like(value), func.lower(Task.description).like(value)))

        query = select(Task)
        count_query = select(func.count()).select_from(Task)
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(Task.due_date.asc().nullslast(), Task.id).limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_task(self, data: dict[str, Any]) -> Task:
        task = Task(**data)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        await event_bus.publish(
            TaskAssigned(
                task_id=task.id,
                assigned_to=task.assigned_to,
                assigned_by=task.assigned_by,
                title=task.title,
            ),
            self.session,
        )
        return task

    async def update_task(self, task: Task, data: dict[str, Any]) -> Task:
        reject_null_on_required(Task, data)
        for key, value in data.items():
            setattr(task, key, value)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def delete_task(self, task: Task) -> Task:
        await self.session.delete(task)
        await self.session.commit()
        return task


async def get_task_service(session: AsyncSession = Depends(get_db_session)) -> TaskService:
    return TaskService(session)
