"""The student's activity feed.

Phase 6, eighth module. No new tables — a projection over `ActivityLog`
(scoped to the student via the entity it names, since `ActivityLog` itself has
no `student_id`) and `ApplicationStatusHistory` (already `student_id`-free too,
scoped via a join through `Application`).

`ActivityLog` currently only gains rows for documents and application status
changes (`app/core/subscribers.py`), and application status changes are
deliberately read from `ApplicationStatusHistory` instead — the same event
writes both, `ApplicationStatusHistory` carries the richer `old_status`, and
reading both would show every transition twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..models import Application, ApplicationStatusHistory, Document, User
from ..models.system import ActivityLog


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    id: UUID
    type: str
    message: str
    created_at: datetime


class ActivityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for(self, student: User, limit: int = 50) -> list[ActivityEntry]:
        document_rows = await self.session.execute(
            select(ActivityLog.id, ActivityLog.description, ActivityLog.created_at)
            .join(Document, ActivityLog.entity_id == Document.id)
            .where(ActivityLog.entity_type == "document", Document.student_id == student.id)
        )
        document_entries = [
            ActivityEntry(id=row.id, type="document", message=row.description or "", created_at=row.created_at)
            for row in document_rows
        ]

        status_rows = await self.session.execute(
            select(
                ApplicationStatusHistory.id,
                ApplicationStatusHistory.old_status,
                ApplicationStatusHistory.new_status,
                ApplicationStatusHistory.created_at,
            )
            .join(Application, ApplicationStatusHistory.application_id == Application.id)
            .where(Application.student_id == student.id)
        )
        status_entries = [
            ActivityEntry(
                id=row.id,
                type="application",
                message=(
                    f"Application moved from {row.old_status} to {row.new_status}"
                    if row.old_status
                    else f"Application created with status {row.new_status}"
                ),
                created_at=row.created_at,
            )
            for row in status_rows
        ]

        combined = sorted(document_entries + status_entries, key=lambda entry: entry.created_at, reverse=True)
        return combined[:limit]


async def get_activity_service(session: AsyncSession = Depends(get_db_session)) -> ActivityService:
    return ActivityService(session)
