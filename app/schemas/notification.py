from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import NotificationChannel, NotificationType


class NotificationBase(BaseModel):
    user_id: UUID
    type: NotificationType
    channel: NotificationChannel | None = NotificationChannel.IN_APP
    title: str
    message: str
    is_read: bool | None = False
    read_at: datetime | None = None


class NotificationCreate(NotificationBase):
    pass


class NotificationUpdate(BaseModel):
    user_id: UUID | None = None
    type: NotificationType | None = None
    channel: NotificationChannel | None = None
    title: str | None = None
    message: str | None = None
    is_read: bool | None = None
    read_at: datetime | None = None


class NotificationRead(NotificationBase):
    id: UUID
    created_at: datetime | None
    #: What the notification is about, and where pressing it should go.
    #:
    #: On the read schema only — deliberately not on `NotificationCreate`.
    #: These are written by the server, which is what knows which surface owns
    #: an entity; letting a caller name an `action_url` would be an open
    #: redirect wearing a notification.
    related_type: str | None = None
    related_id: UUID | None = None
    action_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class NotificationList(BaseModel):
    items: list[NotificationRead]
    total: int
    page: int
    limit: int
