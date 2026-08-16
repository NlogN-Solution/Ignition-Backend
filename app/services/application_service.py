from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..core.events import ApplicationCreated, ApplicationStatusChanged, event_bus
from ..models import Application, ApplicationStatusHistory
from ..models.enums import ApplicationStatus
from .partial_update import reject_null_on_required


class ApplicationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_application(self, application_id: UUID) -> Application | None:
        return await self.session.scalar(select(Application).where(Application.id == application_id))

    async def list_applications(
        self,
        page: int,
        limit: int,
        student_id: UUID | None = None,
        counsellor_id: UUID | None = None,
        program_id: UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[Application], int]:
        # ED360 builds the filters twice, once for the page and once for the
        # count, which is how the two drift apart.
        conditions: list[ColumnElement[bool]] = []
        if student_id:
            conditions.append(Application.student_id == student_id)
        if counsellor_id:
            conditions.append(Application.counsellor_id == counsellor_id)
        if program_id:
            conditions.append(Application.program_id == program_id)
        if status:
            conditions.append(Application.status == status)

        query = select(Application)
        count_query = select(func.count()).select_from(Application)
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        # Unordered in ED360, which makes pagination non-deterministic.
        query = query.order_by(Application.created_at.desc(), Application.id).limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_application(self, data: dict[str, Any]) -> Application:
        application = Application(**data)
        self.session.add(application)
        await self.session.commit()
        await self.session.refresh(application)
        await event_bus.publish(
            ApplicationCreated(
                application_id=application.id,
                student_id=application.student_id,
                created_by=application.counsellor_id,
            ),
            self.session,
        )
        return application

    async def update_application(self, application: Application, data: dict[str, Any]) -> Application:
        """Field updates only.

        `status` is deliberately not settable here — see `ApplicationUpdate`.
        It moves through `change_application_status`, which is what writes the
        history row.
        """
        reject_null_on_required(Application, data)
        for key, value in data.items():
            setattr(application, key, value)

        await self.session.commit()
        await self.session.refresh(application)
        return application

    async def change_application_status(
        self,
        application: Application,
        new_status: ApplicationStatus,
        performed_by: UUID | None = None,
        remarks: str | None = None,
    ) -> Application:
        old_status = application.status
        if new_status == old_status:
            return application

        application.status = new_status
        self.session.add(
            ApplicationStatusHistory(
                application_id=application.id,
                old_status=old_status,
                new_status=new_status,
                changed_by=performed_by,
                remarks=remarks,
            )
        )
        await self.session.commit()
        await self.session.refresh(application)

        # After commit: subscribers read the database and must see this change.
        await event_bus.publish(
            ApplicationStatusChanged(
                application_id=application.id,
                student_id=application.student_id,
                old_status=old_status.value if old_status else None,
                new_status=new_status.value,
                changed_by=performed_by,
                remarks=remarks,
            ),
            self.session,
        )
        return application

    async def list_status_history(self, application_id: UUID) -> list[ApplicationStatusHistory]:
        result = await self.session.execute(
            select(ApplicationStatusHistory)
            .where(ApplicationStatusHistory.application_id == application_id)
            .order_by(ApplicationStatusHistory.created_at)
        )
        return list(result.scalars().all())

    async def delete_application(self, application: Application) -> Application:
        await self.session.delete(application)
        await self.session.commit()
        return application


async def get_application_service(session: AsyncSession = Depends(get_db_session)) -> ApplicationService:
    return ApplicationService(session)
