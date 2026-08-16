from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MilestoneRead(BaseModel):
    """A rung on the journey plus whether this student has reached it.

    Flattened deliberately: the portal renders one list, and making the client
    join a catalog against a per-student table would be busywork.
    """

    key: str
    label: str
    description: str | None = None
    weight: int
    order: int
    is_complete: bool = False
    completed_at: datetime | None = None


class ProgressRead(BaseModel):
    completion_percentage: int
    next_milestone: MilestoneRead | None = None
    milestones: list[MilestoneRead]


class PointsEntryRead(BaseModel):
    id: UUID
    action: str
    points: int
    description: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PointsRead(BaseModel):
    balance: int
    history: list[PointsEntryRead]
