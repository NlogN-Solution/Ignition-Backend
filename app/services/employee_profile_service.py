from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import Department, EmployeeEmploymentEvent, EmployeeProfile, User
from ..models.enums import EmploymentEventType, UserRole
from .partial_update import reject_null_on_required

#: Field changes worth recording on the employee's lifecycle timeline.
TRACKED_FIELDS: dict[str, EmploymentEventType] = {
    "department_id": EmploymentEventType.DEPARTMENT_CHANGED,
    "manager_id": EmploymentEventType.MANAGER_CHANGED,
    "designation": EmploymentEventType.DESIGNATION_CHANGED,
    "employment_status": EmploymentEventType.STATUS_CHANGED,
}


class EmployeeProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> EmployeeProfile | None:
        # `department_ref` is eager-loaded because `EmployeeProfile.department_name`
        # reads it; a lazy load there would raise under async SQLAlchemy.
        return await self.session.scalar(
            select(EmployeeProfile)
            .options(selectinload(EmployeeProfile.department_ref))
            .where(EmployeeProfile.user_id == user_id)
        )

    async def upsert(
        self,
        user_id: UUID,
        data: dict[str, Any],
        changed_by: UUID | None = None,
    ) -> EmployeeProfile:
        profile = await self.get_by_user_id(user_id)
        pending_events: list[EmployeeEmploymentEvent] = []

        if profile is None:
            profile = EmployeeProfile(user_id=user_id, **data)
            self.session.add(profile)
            await self.session.flush()
            pending_events.append(
                EmployeeEmploymentEvent(
                    employee_profile_id=profile.id,
                    event_type=EmploymentEventType.JOINED,
                    description="Employee profile created",
                    changed_by=changed_by,
                )
            )
        else:
            reject_null_on_required(EmployeeProfile, data)
            for key, value in data.items():
                if key in TRACKED_FIELDS:
                    previous = getattr(profile, key)
                    if previous != value:
                        pending_events.append(
                            EmployeeEmploymentEvent(
                                employee_profile_id=profile.id,
                                event_type=TRACKED_FIELDS[key],
                                changed_by=changed_by,
                                previous_value=str(previous) if previous is not None else None,
                                # None where a field was cleared, so the
                                # timeline distinguishes "moved to X" from
                                # "removed from a department".
                                new_value=str(value) if value is not None else None,
                            )
                        )
                setattr(profile, key, value)

        for event in pending_events:
            self.session.add(event)

        await self.session.commit()
        # Re-read through `get_by_user_id` so `department_ref` is loaded for the
        # response: `refresh` would leave a just-changed `department_id` with a
        # stale or missing relationship.
        refreshed = await self.get_by_user_id(user_id)
        assert refreshed is not None
        return refreshed

    async def list_events(self, employee_profile_id: UUID) -> list[EmployeeEmploymentEvent]:
        result = await self.session.execute(
            select(EmployeeEmploymentEvent)
            .where(EmployeeEmploymentEvent.employee_profile_id == employee_profile_id)
            .order_by(EmployeeEmploymentEvent.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_directory(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        department_id: UUID | None = None,
        employment_status: str | None = None,
    ) -> tuple[list[Any], int]:
        """Every staff account (role != student), left-joined against
        EmployeeProfile and Department.

        The outer joins are load-bearing: a staff account whose employee profile
        has not been filled in yet must still appear in the directory, with
        those columns null, rather than vanishing from it.
        """

        def _base_query(*select_columns: Any) -> Select[Any]:
            query = (
                select(*select_columns)
                .select_from(User)
                .outerjoin(EmployeeProfile, EmployeeProfile.user_id == User.id)
                .outerjoin(Department, Department.id == EmployeeProfile.department_id)
                .where(User.role != UserRole.STUDENT.value, User.deleted_at.is_(None))
            )
            if department_id is not None:
                query = query.where(EmployeeProfile.department_id == department_id)
            if employment_status:
                query = query.where(EmployeeProfile.employment_status == employment_status)
            if search and search.strip():
                search_value = f"%{search.strip().lower()}%"
                query = query.where(
                    or_(
                        func.lower(User.first_name).like(search_value),
                        func.lower(User.last_name).like(search_value),
                        func.lower(User.email).like(search_value),
                        func.lower(User.phone).like(search_value),
                        func.lower(EmployeeProfile.employee_code).like(search_value),
                    )
                )
            return query

        total = await self.session.scalar(select(func.count()).select_from(_base_query(User.id).subquery()))

        query = (
            _base_query(
                User.id,
                User.first_name,
                User.last_name,
                User.email,
                User.phone,
                User.avatar_url,
                User.role,
                User.status,
                EmployeeProfile.employee_code,
                EmployeeProfile.designation,
                EmployeeProfile.department_id,
                func.coalesce(Department.name, EmployeeProfile.department).label("department_name"),
                EmployeeProfile.employment_status,
                EmployeeProfile.employment_type,
                EmployeeProfile.joining_date,
            )
            .order_by(User.first_name, User.last_name, User.id)
            .limit(limit)
            .offset((page - 1) * limit)
        )
        result = await self.session.execute(query)
        return list(result.mappings().all()), total or 0


async def get_employee_profile_service(session: AsyncSession = Depends(get_db_session)) -> EmployeeProfileService:
    return EmployeeProfileService(session)
