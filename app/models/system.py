from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import UUIDPKMixin
from ..db.types import enum_type
from .enums import ActivityType

if TYPE_CHECKING:
    from .user import User


class UserSession(Base, UUIDPKMixin):
    """A live refresh token.

    ED360 defines this table and never reads or writes it, which is why a
    stolen refresh token there survives logout and password changes for its
    full 30 days. Phase 2 wires it up here: persist on login, rotate on
    refresh, revoke on logout, revoke-all on password change.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="user_sessions")

    __table_args__ = (
        Index("idx_user_sessions_user_id", "user_id"),
        Index("idx_user_sessions_expires_at", "expires_at"),
        Index("idx_user_sessions_refresh_token_hash", "refresh_token_hash"),
    )

    def __repr__(self) -> str:
        return f"<UserSession id={self.id} user_id={self.user_id}>"


class ActivityLog(Base, UUIDPKMixin):
    __tablename__ = "activity_logs"

    # Nullable: a failed login has no resolvable user, and that is exactly the
    # event most worth recording.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    activity_type: Mapped[ActivityType] = mapped_column(
        enum_type(ActivityType, "activity_type", create_type=False),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    description: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User | None] = relationship(back_populates="activity_logs")

    __table_args__ = (
        Index("idx_activity_logs_user_id", "user_id"),
        Index("idx_activity_logs_activity_type", "activity_type"),
        Index("idx_activity_logs_entity_type", "entity_type"),
        Index("idx_activity_logs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ActivityLog id={self.id} activity_type={self.activity_type}>"
