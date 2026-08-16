from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .academic import ProgramRead, UniversityRead


class SaveItemRequest(BaseModel):
    """Body for saving or comparing an item.

    The student is never part of the payload — it comes from the token, so one
    student cannot save an item onto another's list.
    """

    item_id: UUID


class SavedCourseRead(BaseModel):
    id: UUID
    program_id: UUID
    created_at: datetime
    program: ProgramRead | None = None

    model_config = ConfigDict(from_attributes=True)


class SavedUniversityRead(BaseModel):
    id: UUID
    university_id: UUID
    created_at: datetime
    university: UniversityRead | None = None

    model_config = ConfigDict(from_attributes=True)


class SavedCourseList(BaseModel):
    items: list[SavedCourseRead]
    total: int


class SavedUniversityList(BaseModel):
    items: list[SavedUniversityRead]
    total: int


class CompareCourseList(BaseModel):
    items: list[SavedCourseRead]
    total: int
    #: Surfaced so the UI can disable the button rather than discover the cap
    #: through a 400.
    max_items: int
