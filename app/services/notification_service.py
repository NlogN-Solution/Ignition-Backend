from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..models import Notification
from ..models.enums import NotificationType
from .partial_update import reject_null_on_required


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_notification(self, notification_id: UUID) -> Notification | None:
        return await self.session.scalar(select(Notification).where(Notification.id == notification_id))

    async def list_notifications(
        self,
        page: int,
        limit: int,
        user_id: UUID | None = None,
        notification_type: str | None = None,
        channel: str | None = None,
        is_read: bool | None = None,
    ) -> tuple[list[Notification], int]:
        conditions: list[ColumnElement[bool]] = []
        if user_id:
            conditions.append(Notification.user_id == user_id)
        if notification_type:
            conditions.append(Notification.type == notification_type)
        if channel:
            conditions.append(Notification.channel == channel)
        if is_read is not None:
            conditions.append(Notification.is_read == is_read)

        query = select(Notification)
        count_query = select(func.count()).select_from(Notification)
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(Notification.created_at.desc(), Notification.id).limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_notification(self, data: dict[str, Any]) -> Notification:
        notification = Notification(**data)
        self.session.add(notification)
        await self.session.commit()
        await self.session.refresh(notification)
        return notification

    async def notify_many(
        self,
        user_ids: Iterable[UUID],
        *,
        notification_type: NotificationType,
        title: str,
        message: str,
    ) -> None:
        """Fan one notification out to several recipients in a single commit.

        ED360's callers loop and commit per recipient, so a failure part-way
        leaves some staff notified and the rest not.
        """
        recipients = list(user_ids)
        if not recipients:
            return
        for user_id in recipients:
            self.session.add(
                Notification(
                    user_id=user_id,
                    type=notification_type,
                    title=title,
                    message=message,
                )
            )
        await self.session.commit()

    async def update_notification(self, notification: Notification, data: dict[str, Any]) -> Notification:
        reject_null_on_required(Notification, data)
        for key, value in data.items():
            setattr(notification, key, value)
        await self.session.commit()
        await self.session.refresh(notification)
        return notification

    async def set_notification_read_status(self, notification: Notification, is_read: bool) -> Notification:
        notification.is_read = is_read
        await self.session.commit()
        await self.session.refresh(notification)
        return notification

    async def delete_notification(self, notification: Notification) -> Notification:
        await self.session.delete(notification)
        await self.session.commit()
        return notification


async def get_notification_service(session: AsyncSession = Depends(get_db_session)) -> NotificationService:
    return NotificationService(session)
