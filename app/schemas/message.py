from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageSenderSummary(BaseModel):
    id: UUID
    full_name: str


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class MessageRead(BaseModel):
    id: UUID
    is_from_student: bool
    body: str
    is_read: bool
    created_at: datetime
    sender: MessageSenderSummary | None = Field(default=None, validation_alias="_sender_summary")

    model_config = ConfigDict(from_attributes=True)


class MessageList(BaseModel):
    items: list[MessageRead]
    total: int
    unread_count: int


class MessageThreadSummary(BaseModel):
    """One row per student with at least one message — the staff inbox list.

    `unread_count` here is the mirror image of `MessageList.unread_count`:
    that one counts staff messages the student hasn't read, this one counts
    student messages no staff member has read yet.
    """

    student_id: UUID
    student_name: str
    last_message: str
    last_message_from_student: bool
    last_message_at: datetime
    unread_count: int
