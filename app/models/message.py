from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class Message(Base, UUIDPKMixin):
    """One student-support thread per student — any staff member can see and
    reply to it, matching how applications/appointments start unassigned.
    No `updated_at`: a chat message is never edited, only sent."""

    __tablename__ = "messages"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_from_student: Mapped[bool] = mapped_column(Boolean, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Whether the *recipient* has read this message — the student for a
    #: staff-authored row, any staff member for a student-authored one.
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    student: Mapped[User] = relationship(foreign_keys=[student_id])
    sender: Mapped[User] = relationship(foreign_keys=[sender_id])

    __table_args__ = (
        Index("idx_messages_student_id", "student_id"),
        Index("idx_messages_student_unread", "student_id", "is_read", postgresql_where=text("is_read = FALSE")),
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} student_id={self.student_id}>"
