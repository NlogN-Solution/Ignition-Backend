from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActivityEntryRead(BaseModel):
    id: UUID
    type: str
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ActivityList(BaseModel):
    items: list[ActivityEntryRead]
    total: int
