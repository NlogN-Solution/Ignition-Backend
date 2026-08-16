from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..models import Department, EmployeeProfile
from .partial_update import reject_null_on_required


class DepartmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, department_id: UUID) -> Department | None:
        return await self.session.scalar(select(Department).where(Department.id == department_id))

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
    ) -> tuple[list[Department], int]:
        query = select(Department)
        count_query = select(func.count()).select_from(Department)

        if search and search.strip():
            search_value = f"%{search.strip().lower()}%"
            search_filter = or_(
                func.lower(Department.name).like(search_value),
                func.lower(Department.description).like(search_value),
            )
            query = query.where(search_filter)
            count_query = count_query.where(search_filter)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(Department.name).offset((page - 1) * limit).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def employee_counts(self, department_ids: Sequence[UUID]) -> dict[UUID, int]:
        """Employee counts for a batch of departments in one query — the list
        view would otherwise issue one count per row.

        `Sequence[UUID]`, not `list[UUID]`: this class defines a method named
        `list`, which shadows the builtin for annotations evaluated in class
        scope after that definition.
        """
        if not department_ids:
            return {}
        result = await self.session.execute(
            select(EmployeeProfile.department_id, func.count(EmployeeProfile.id))
            .where(EmployeeProfile.department_id.in_(department_ids))
            .group_by(EmployeeProfile.department_id)
        )
        return dict(result.all())  # type: ignore[arg-type]

    async def create(self, data: dict[str, Any]) -> Department:
        department = Department(**data)
        self.session.add(department)
        await self.session.commit()
        await self.session.refresh(department)
        return department

    async def update(self, department: Department, data: dict[str, Any]) -> Department:
        reject_null_on_required(Department, data)
        for key, value in data.items():
            setattr(department, key, value)
        await self.session.commit()
        await self.session.refresh(department)
        return department

    async def delete(self, department: Department) -> Department:
        await self.session.delete(department)
        await self.session.commit()
        return department


async def get_department_service(session: AsyncSession = Depends(get_db_session)) -> DepartmentService:
    return DepartmentService(session)
