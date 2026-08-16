from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import ActivityLog
from ..models.enums import ActivityType


class ActivityLogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self,
        *,
        user_id: UUID | None,
        activity_type: ActivityType | str,
        entity_type: str,
        entity_id: UUID | None = None,
        description: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ActivityLog:
        entry = ActivityLog(
            user_id=user_id,
            activity_type=activity_type,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def list_logs(
        self,
        page: int,
        limit: int,
        user_id: UUID | None = None,
        activity_type: str | None = None,
        entity_type: str | None = None,
    ) -> tuple[Sequence[ActivityLog], int]:
        query = select(ActivityLog).options(selectinload(ActivityLog.user))
        count_query = select(func.count()).select_from(ActivityLog)

        if user_id:
            query = query.where(ActivityLog.user_id == user_id)
            count_query = count_query.where(ActivityLog.user_id == user_id)
        if activity_type:
            query = query.where(ActivityLog.activity_type == activity_type)
            count_query = count_query.where(ActivityLog.activity_type == activity_type)
        if entity_type:
            query = query.where(ActivityLog.entity_type == entity_type)
            count_query = count_query.where(ActivityLog.entity_type == entity_type)

        query = query.order_by(ActivityLog.created_at.desc()).offset((page - 1) * limit).limit(limit)

        total = await self.session.scalar(count_query) or 0
        result = await self.session.execute(query)
        return result.scalars().all(), total


async def get_activity_log_service(session: AsyncSession = Depends(get_db_session)) -> ActivityLogService:
    return ActivityLogService(session)
