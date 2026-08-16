from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from ..models.enums import ActivityType


class ActivityLogRead(BaseModel):
    id: UUID
    user_id: UUID | None
    user_name: str | None = None
    user_email: str | None = None
    activity_type: ActivityType
    entity_type: str
    entity_id: UUID | None
    description: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class ActivityLogList(BaseModel):
    items: list[ActivityLogRead]
    total: int
    page: int
    limit: int
