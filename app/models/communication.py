"""Correspondence, as one history per person.

## What was here before

`Message` was a flat table keyed by `student_id`: body, sender, read flag. One
implicit conversation per student, no subject, no attachments, and — the part
that mattered — **no link to the lead the student used to be**. Lead-stage
correspondence lived in `LeadActivity` instead, a different table with a
different shape, written by a different screen.

So the moment a lead converted, their history stopped. A counsellor who had
spent three weeks talking someone into applying opened the new student's
record and found an empty inbox. The conversation had not been deleted; it was
filed under an identity the student page could not see.

## The fix

One `MessageThread`, which may point at a lead, a student, or both — and
resolution is by **person**, not by lifecycle stage. `Lead.converted_user_id`
already records that a lead became a user, so a thread opened against the lead
is still that user's thread afterwards. Nothing is copied, nothing is
migrated on conversion, and there is exactly one row for the conversation
however far through the journey the person is when you look at it.

That is why `student_id` and `lead_id` are both nullable and neither is the
"real" one. A thread has a *subject* and belongs to a *person*; which columns
happen to be filled is a consequence of when it started.

## Internal notes

`visibility` is what keeps staff able to talk about a student in the same
place they talk *to* them, without a mis-click showing the student a note
about their own file. `INTERNAL` threads are filtered out of every
student-facing read, structurally, in `CommunicationService`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, BigInteger, Boolean, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type

if TYPE_CHECKING:
    from .application import Application
    from .lead import Lead
    from .user import User


class ThreadVisibility(str, Enum):
    #: Both sides can read it. The default, because the overwhelming majority
    #: of correspondence is *with* the student.
    SHARED = "shared"
    #: Staff only. Never returned by a student-facing endpoint.
    INTERNAL = "internal"


class MessageAttachmentKind(str, Enum):
    FILE = "file"
    IMAGE = "image"
    #: A recorded voice note. Same storage, different affordance on screen —
    #: a player rather than a download link.
    VOICE = "voice"


class MessageThread(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "message_threads"

    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The person. At least one of these is always set; both are set for a
    #: thread that began before conversion and continued after it.
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))

    #: Optional. Set when the correspondence is about one application — which
    #: is what lets the application page show its own subset without a second
    #: store of messages.
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"))

    visibility: Mapped[ThreadVisibility] = mapped_column(
        enum_type(ThreadVisibility, "thread_visibility", create_type=False),
        nullable=False,
        server_default=ThreadVisibility.SHARED.value,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    #: Denormalised so the mailbox list can sort and preview without touching
    #: `messages`. Maintained by `CommunicationService` on every send.
    last_message_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_message_preview: Mapped[str | None] = mapped_column(String(300))

    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    student: Mapped[User | None] = relationship("User", foreign_keys=[student_id])
    lead: Mapped[Lead | None] = relationship("Lead", foreign_keys=[lead_id])
    application: Mapped[Application | None] = relationship("Application")
    author: Mapped[User | None] = relationship("User", foreign_keys=[created_by])
    messages: Mapped[list[ThreadMessage]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ThreadMessage.created_at",
    )

    __table_args__ = (
        Index("idx_message_threads_student_id", "student_id"),
        Index("idx_message_threads_lead_id", "lead_id"),
        Index("idx_message_threads_application_id", "application_id"),
        Index("idx_message_threads_last_message_at", "last_message_at"),
    )

    def __repr__(self) -> str:
        return f"<MessageThread id={self.id} subject={self.subject!r}>"


class ThreadMessage(Base, UUIDPKMixin):
    """One message in a thread.

    No `updated_at`: correspondence is not edited. A correction is another
    message, which is how it works on paper and how it has to work here for
    the timeline to mean anything.
    """

    __tablename__ = "thread_messages"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Nullable because a staff account can be deleted and the message must
    #: survive it — the thread is a record of what was said, not of who is
    #: still employed.
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    #: Snapshotted so a deleted author still has a name on their messages.
    author_name: Mapped[str | None] = mapped_column(String(200))
    #: Whether the *student side* wrote it. Kept as a column rather than
    #: derived from the author's role, because a lead-stage message may have
    #: no user account behind it at all.
    is_from_student: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    #: Plain text, always populated — it is what search, previews and
    #: notifications read, and what a client with no rich renderer falls back
    #: to. `body_html` is the sanitised rich version when there is one.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text)

    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    thread: Mapped[MessageThread] = relationship(back_populates="messages")
    author: Mapped[User | None] = relationship("User")
    attachments: Mapped[list[MessageAttachment]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_thread_messages_thread_id", "thread_id", "created_at"),
        Index("idx_thread_messages_author_id", "author_id"),
    )

    def __repr__(self) -> str:
        return f"<ThreadMessage id={self.id} thread_id={self.thread_id}>"


class MessageAttachment(Base, UUIDPKMixin):
    """A file on a message.

    Deliberately **not** a `Document`. `documents` is the student's paperwork
    vault — the passports and transcripts staff verify — and a screenshot
    pasted into a reply has no business appearing in a verification queue. Same
    storage path (`core/uploads`, private, signed access), different table,
    different meaning.
    """

    __tablename__ = "message_attachments"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("thread_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[MessageAttachmentKind] = mapped_column(
        enum_type(MessageAttachmentKind, "message_attachment_kind", create_type=False),
        nullable=False,
        server_default=MessageAttachmentKind.FILE.value,
    )
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    #: Seconds, for voice notes. NULL for everything else.
    duration_seconds: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    message: Mapped[ThreadMessage] = relationship(back_populates="attachments")

    __table_args__ = (Index("idx_message_attachments_message_id", "message_id"),)

    def __repr__(self) -> str:
        return f"<MessageAttachment id={self.id} kind={self.kind}>"
