from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import UUIDPKMixin
from ..db.types import enum_type
from .enums import NotificationChannel, NotificationType

if TYPE_CHECKING:
    from .user import User


class Notification(Base, UUIDPKMixin):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[NotificationType] = mapped_column(
        enum_type(NotificationType, "notification_type", create_type=False),
        nullable=False,
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        enum_type(NotificationChannel, "notification_channel", create_type=False),
        nullable=False,
        server_default=NotificationChannel.IN_APP.value,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: What the notification is *about*, so a client can route to it without
    #: parsing the title. Before these existed every consumer either guessed
    #: from the text or gave the student a notification they could not act on.
    #:
    #: A loose (type, id) pair rather than a nullable FK per entity: adding a
    #: notifiable kind should not mean another migration and another mostly-NULL
    #: column, and there is no referential guarantee worth having here — a
    #: notification about a deleted application is still a true record that it
    #: was sent.
    related_type: Mapped[str | None] = mapped_column(String(40))
    related_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Where pressing it should go, as a client-relative path. Written by the
    #: server because the server is what knows which surface owns the entity.
    action_url: Mapped[str | None] = mapped_column(String(300))

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="notifications")

    __table_args__ = (
        Index("idx_notifications_user_id", "user_id"),
        Index("idx_notifications_created_at", "created_at"),
        Index("idx_notifications_user_unread", "user_id", "is_read", postgresql_where=text("is_read = FALSE")),
        Index("idx_notifications_related", "related_type", "related_id"),
    )

    def __repr__(self) -> str:
        return f"<Notification id={self.id} title={self.title}>"
