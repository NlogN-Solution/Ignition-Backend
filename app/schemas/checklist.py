from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChecklistItemRead(BaseModel):
    id: UUID
    key: str | None = None
    title: str
    description: str | None = None
    stage: str | None = None
    order: int
    depends_on_key: str | None = None
    due_date: date | None = None
    is_complete: bool = False
    completed_at: datetime | None = None
    is_custom: bool = False
    #: Computed per response, not stored: whether the prerequisite is still
    #: outstanding depends on the rest of the list, and a stored flag would go
    #: stale the moment the item it depends on is ticked.
    is_locked: bool = False

    model_config = ConfigDict(from_attributes=True)


class ChecklistRead(BaseModel):
    items: list[ChecklistItemRead]
    total: int
    completed: int


class ChecklistItemCreate(BaseModel):
    """A student's own addition. No key, no stage, no dependency — those belong
    to the seeded journey, and letting a client set them would let it insert
    itself into the ladder."""

    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    due_date: date | None = None


class ChecklistItemUpdate(BaseModel):
    completed: bool | None = None
    due_date: date | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
