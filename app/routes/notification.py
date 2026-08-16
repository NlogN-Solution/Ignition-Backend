from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import get_current_user, require_role
from ..api.exceptions import ForbiddenException, NotFoundException
from ..models import Notification, User
from ..models.enums import UserRole
from ..schemas.notification import (
    NotificationCreate,
    NotificationList,
    NotificationRead,
    NotificationUpdate,
)
from ..services.notification_service import NotificationService, get_notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _assert_owner_or_admin(user: User, notification: Notification) -> None:
    if notification.user_id != user.id and user.role is not UserRole.ADMIN:
        raise ForbiddenException("Forbidden")


@router.get("", response_model=NotificationList, summary="List my notifications")
async def list_notifications(
    page: int = 1,
    limit: int = 20,
    notification_type: str | None = None,
    channel: str | None = None,
    is_read: bool | None = None,
    service: NotificationService = Depends(get_notification_service),
    user: User = Depends(get_current_user),
) -> NotificationList:
    """Always scoped to the caller — there is no way to ask for someone else's."""
    notifications, total = await service.list_notifications(
        page,
        limit,
        user_id=user.id,
        notification_type=notification_type,
        channel=channel,
        is_read=is_read,
    )
    return NotificationList(
        items=[NotificationRead.model_validate(n) for n in notifications],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{notification_id}", response_model=NotificationRead, summary="Get notification")
async def get_notification(
    notification_id: UUID,
    service: NotificationService = Depends(get_notification_service),
    user: User = Depends(get_current_user),
) -> NotificationRead:
    notification = await service.get_notification(notification_id)
    if notification is None:
        raise NotFoundException("Notification not found")
    _assert_owner_or_admin(user, notification)
    return NotificationRead.model_validate(notification)


@router.post("", response_model=NotificationRead, summary="Create notification")
async def create_notification(
    payload: NotificationCreate,
    service: NotificationService = Depends(get_notification_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> NotificationRead:
    """Admin only.

    ED360 guards this with a bare `get_current_user` while `user_id` is a body
    field, so any account can post a notification that appears, with full
    system authority, in anyone else's feed — "Your visa was approved, pay here"
    from one student to another. Ordinary notifications are raised by the
    services that own the event, not over HTTP.
    """
    return NotificationRead.model_validate(await service.create_notification(payload.model_dump()))


@router.post("/{notification_id}/read", response_model=NotificationRead, summary="Mark as read")
async def mark_notification_read(
    notification_id: UUID,
    service: NotificationService = Depends(get_notification_service),
    user: User = Depends(get_current_user),
) -> NotificationRead:
    notification = await service.get_notification(notification_id)
    if notification is None:
        raise NotFoundException("Notification not found")
    _assert_owner_or_admin(user, notification)
    return NotificationRead.model_validate(await service.set_notification_read_status(notification, True))


@router.post("/{notification_id}/unread", response_model=NotificationRead, summary="Mark as unread")
async def mark_notification_unread(
    notification_id: UUID,
    service: NotificationService = Depends(get_notification_service),
    user: User = Depends(get_current_user),
) -> NotificationRead:
    notification = await service.get_notification(notification_id)
    if notification is None:
        raise NotFoundException("Notification not found")
    _assert_owner_or_admin(user, notification)
    return NotificationRead.model_validate(await service.set_notification_read_status(notification, False))


@router.patch("/{notification_id}", response_model=NotificationRead, summary="Update notification")
async def update_notification(
    notification_id: UUID,
    payload: NotificationUpdate,
    service: NotificationService = Depends(get_notification_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> NotificationRead:
    notification = await service.get_notification(notification_id)
    if notification is None:
        raise NotFoundException("Notification not found")
    updated = await service.update_notification(notification, payload.model_dump(exclude_unset=True))
    return NotificationRead.model_validate(updated)


@router.delete("/{notification_id}", response_model=NotificationRead, summary="Delete notification")
async def delete_notification(
    notification_id: UUID,
    service: NotificationService = Depends(get_notification_service),
    user: User = Depends(get_current_user),
) -> NotificationRead:
    """Your own notification, or anyone's if you are an admin — ED360 restricts
    this to admins, which leaves a user unable to dismiss their own."""
    notification = await service.get_notification(notification_id)
    if notification is None:
        raise NotFoundException("Notification not found")
    _assert_owner_or_admin(user, notification)
    return NotificationRead.model_validate(await service.delete_notification(notification))
