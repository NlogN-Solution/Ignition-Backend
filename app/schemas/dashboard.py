from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from .activity import ActivityEntryRead
from .progress import MilestoneRead


class DashboardApplicationSummary(BaseModel):
    id: UUID
    status: str
    program_name: str | None = None


class DashboardAppointmentSummary(BaseModel):
    id: UUID
    title: str
    start_time: datetime | None = None


class DashboardRead(BaseModel):
    completion_percentage: int
    next_milestone: MilestoneRead | None = None
    points_balance: int
    active_applications: list[DashboardApplicationSummary]
    upcoming_appointments: list[DashboardAppointmentSummary]
    pending_documents_count: int
    unread_notifications_count: int
    recent_activity: list[ActivityEntryRead]
    #: Present only when this response was served from cache — the route
    #: leaves it unset on a fresh compute so callers can tell the two apart
    #: without a separate header.
    cached: bool = False
