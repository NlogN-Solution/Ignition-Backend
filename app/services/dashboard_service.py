"""The student portal's home screen, in one call.

Phase 6, tenth and final module. Everything here already exists as its own
endpoint — this is a fan-out that answers "what does my dashboard show"
without the client making eight requests and assembling it client-side.

Redis-cached (`core/cache.py`) because a fan-out over seven tables on every
page load is the expensive kind of cheap. The cache is invalidated by the
event subscribers that already fire for everything that could change it
(`app/core/subscribers.py`) rather than left to the TTL alone — so a student
who just got a document approved sees it immediately, not up to sixty seconds
later.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..core.cache import dashboard_cache_key, get_json, set_json
from ..models import Application, Appointment, Document, Notification, User
from ..models.enums import ApplicationStatus, AppointmentStatus, DocumentStatus
from ..schemas.activity import ActivityEntryRead
from ..schemas.dashboard import DashboardApplicationSummary, DashboardAppointmentSummary, DashboardRead
from ..schemas.progress import MilestoneRead
from .activity_service import ActivityService
from .progress_service import PointsService, ProgressService

#: Statuses that no longer represent something the student is actively
#: pursuing — a dashboard tile titled "active applications" showing a
#: withdrawn one would be wrong, not just uninteresting.
_INACTIVE_APPLICATION_STATUSES = frozenset(
    {ApplicationStatus.WITHDRAWN, ApplicationStatus.REJECTED, ApplicationStatus.OFFER_DECLINED}
)
_UPCOMING_APPOINTMENT_STATUSES = frozenset({AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED})
_RECENT_ACTIVITY_LIMIT = 5
_UPCOMING_APPOINTMENTS_LIMIT = 5


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_dashboard(self, student: User) -> DashboardRead:
        cached = await get_json(dashboard_cache_key(student.id))
        if cached is not None:
            return DashboardRead.model_validate({**cached, "cached": True})

        dashboard = await self._compute(student)
        await set_json(dashboard_cache_key(student.id), dashboard.model_dump(mode="json", exclude={"cached"}))
        return dashboard

    async def _compute(self, student: User) -> DashboardRead:
        progress_service = ProgressService(self.session)
        points_service = PointsService(self.session)
        activity_service = ActivityService(self.session)

        pairs = await progress_service.milestones_for(student.id)
        completion_percentage = await progress_service.completion_percentage(student.id)
        next_milestone = next(
            (
                MilestoneRead(
                    key=m.key,
                    label=m.label,
                    description=m.description,
                    weight=m.weight,
                    order=m.order,
                    is_complete=False,
                )
                for m, standing in pairs
                if not (standing and standing.is_complete)
            ),
            None,
        )
        points_balance = await points_service.balance(student.id)

        applications = (
            await self.session.scalars(
                select(Application)
                .where(Application.student_id == student.id)
                .options(selectinload(Application.program))
            )
        ).all()
        active_applications = [
            DashboardApplicationSummary(
                id=a.id, status=a.status.value, program_name=a.program.name if a.program else None
            )
            for a in applications
            if a.status not in _INACTIVE_APPLICATION_STATUSES
        ]

        appointments = (
            await self.session.scalars(
                select(Appointment)
                .where(
                    Appointment.student_id == student.id,
                    Appointment.status.in_(_UPCOMING_APPOINTMENT_STATUSES),
                    Appointment.start_time.is_not(None),
                    Appointment.start_time >= datetime.now(UTC),
                )
                .order_by(Appointment.start_time)
                .limit(_UPCOMING_APPOINTMENTS_LIMIT)
            )
        ).all()
        upcoming_appointments = [
            DashboardAppointmentSummary(id=a.id, title=a.title, start_time=a.start_time) for a in appointments
        ]

        pending_documents_count = await self.session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.student_id == student.id,
                Document.status.in_((DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW)),
            )
        )
        unread_notifications_count = await self.session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == student.id, Notification.is_read.is_(False))
        )

        recent_activity = [
            ActivityEntryRead(id=e.id, type=e.type, message=e.message, created_at=e.created_at)
            for e in await activity_service.list_for(student, limit=_RECENT_ACTIVITY_LIMIT)
        ]

        return DashboardRead(
            completion_percentage=completion_percentage,
            next_milestone=next_milestone,
            points_balance=points_balance,
            active_applications=active_applications,
            upcoming_appointments=upcoming_appointments,
            pending_documents_count=pending_documents_count or 0,
            unread_notifications_count=unread_notifications_count or 0,
            recent_activity=recent_activity,
        )


async def get_dashboard_service(session: AsyncSession = Depends(get_db_session)) -> DashboardService:
    return DashboardService(session)
