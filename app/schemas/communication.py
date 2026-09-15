"""Correspondence on the wire."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.communication import MessageAttachmentKind, ThreadVisibility

_FROM_ORM = ConfigDict(from_attributes=True)


class AttachmentRead(BaseModel):
    id: UUID
    kind: MessageAttachmentKind
    original_file_name: str
    mime_type: str | None = None
    file_size: int | None = None
    duration_seconds: int | None = None
    created_at: datetime

    model_config = _FROM_ORM


class ThreadMessageRead(BaseModel):
    id: UUID
    author_id: UUID | None = None
    author_name: str | None = None
    is_from_student: bool
    body: str
    body_html: str | None = None
    read_at: datetime | None = None
    created_at: datetime
    attachments: list[AttachmentRead] = Field(default_factory=list)

    model_config = _FROM_ORM


class ThreadParticipant(BaseModel):
    """Who the thread is with, resolved however far along they are.

    `stage` is the honest label: a thread may belong to somebody who is still
    a lead, or to a student, and the console shows which without the reader
    having to work it out from which id is null.
    """

    student_id: UUID | None = None
    lead_id: UUID | None = None
    name: str
    email: str | None = None
    phone: str | None = None
    stage: str


class ThreadRead(BaseModel):
    id: UUID
    subject: str
    visibility: ThreadVisibility
    application_id: UUID | None = None
    participant: ThreadParticipant
    last_message_at: datetime
    last_message_preview: str | None = None
    is_closed: bool
    unread_count: int = 0
    message_count: int = 0
    created_at: datetime

    model_config = _FROM_ORM

    @classmethod
    def of(cls, thread: Any, *, viewer_is_student: bool) -> ThreadRead:
        """Summarise a thread for one side of the conversation.

        `unread_count` is viewer-relative — the student counts unread staff
        replies, staff count unread student messages — which is why it is
        computed here from the viewer rather than stored on the row.
        """
        student = thread.student
        lead = thread.lead
        if student is not None:
            participant = ThreadParticipant(
                student_id=student.id,
                lead_id=thread.lead_id,
                name=f"{student.first_name} {student.last_name}".strip(),
                email=student.email,
                phone=student.phone,
                # Both ids set means the conversation predates conversion and
                # carried across — which is exactly the continuity this
                # feature exists for, so it is worth saying out loud.
                stage="student (from lead)" if thread.lead_id else "student",
            )
        elif lead is not None:
            participant = ThreadParticipant(
                lead_id=lead.id,
                name=f"{lead.first_name} {lead.last_name or ''}".strip(),
                email=lead.email,
                phone=lead.phone,
                stage="lead",
            )
        else:
            participant = ThreadParticipant(name="Unknown", stage="unknown")

        messages = thread.messages or []
        return cls(
            id=thread.id,
            subject=thread.subject,
            visibility=thread.visibility,
            application_id=thread.application_id,
            participant=participant,
            last_message_at=thread.last_message_at,
            last_message_preview=thread.last_message_preview,
            is_closed=thread.is_closed,
            unread_count=sum(
                1
                for message in messages
                if message.read_at is None and message.is_from_student is not viewer_is_student
            ),
            message_count=len(messages),
            created_at=thread.created_at,
        )


class ThreadDetail(ThreadRead):
    messages: list[ThreadMessageRead] = Field(default_factory=list)

    @classmethod
    def of(cls, thread: Any, *, viewer_is_student: bool) -> ThreadDetail:
        summary = ThreadRead.of(thread, viewer_is_student=viewer_is_student)
        return cls(
            **summary.model_dump(),
            messages=[ThreadMessageRead.model_validate(m) for m in (thread.messages or [])],
        )


class ThreadList(BaseModel):
    items: list[ThreadRead]
    total: int
    page: int
    limit: int


class ThreadCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    body_html: str | None = None
    #: Exactly one of these identifies the person. Staff-only fields — a
    #: student opening a thread is always opening it about themselves.
    student_id: UUID | None = None
    lead_id: UUID | None = None
    application_id: UUID | None = None
    visibility: ThreadVisibility = ThreadVisibility.SHARED


class StudentThreadCreate(BaseModel):
    """What a student may open.

    No `student_id` — it is theirs by construction — and no `visibility`,
    because an internal note is not something a student can write.
    """

    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    body_html: str | None = None
    application_id: UUID | None = None


# There is no `MessageCreate` schema.
#
# Sending is a **multipart** request — body, optional rich HTML, files and an
# optional voice note in one call — so the fields are declared as `Form`/`File`
# parameters on the route rather than as a Pydantic body.
#
# The alternative was to upload attachments first and pass their ids here. That
# needs either a nullable `message_id` on `message_attachments` (so an abandoned
# upload leaves a row belonging to nothing) or a second "pending" table, and it
# lets a message be created that claims files which then fail to arrive. One
# request keeps the message and its attachments a single outcome: the composer
# holds the files locally until Send, which is what every mail client does.
