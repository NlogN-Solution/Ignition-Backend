"""Event subscribers: the consequences of domain facts.

Phase 5b. Registering here means "when X happens, also do Y" is written in one
place, applies to every caller of the emitting service, and is testable without
going through HTTP.

Each handler receives the emitting session, already committed. Writing through
it opens a fresh transaction, so the change that caused the event is durable
before any subscriber runs and a failing subscriber cannot undo it.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ActivityLog, ApplicationChecklistItem, Notification
from ..models.enums import ActivityType, ChecklistItemStatus, NotificationType
from ..services.progress_service import PointsService, ProgressService
from ..services.staff_resolution import resolve_responsible_staff_ids
from .cache import invalidate_dashboard_cache
from .events import (
    ApplicationStatusChanged,
    AppointmentScheduled,
    ChecklistItemCompleted,
    DocumentApproved,
    DocumentRejected,
    DocumentUploaded,
    Event,
    InterviewCompleted,
    PaymentCompleted,
    TaskAssigned,
    event_bus,
)

logger = logging.getLogger(__name__)


async def _notify(
    session: AsyncSession,
    user_id: UUID,
    notification_type: NotificationType,
    title: str,
    message: str,
) -> None:
    session.add(Notification(user_id=user_id, type=notification_type, title=title, message=message))
    await session.commit()


async def _log(
    session: AsyncSession,
    activity_type: ActivityType,
    entity_type: str,
    entity_id: UUID,
    description: str,
    user_id: UUID | None = None,
) -> None:
    session.add(
        ActivityLog(
            user_id=user_id,
            activity_type=activity_type,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
        )
    )
    await session.commit()


# --- Notifications ------------------------------------------------------------


async def notify_student_of_status_change(event: ApplicationStatusChanged, session: AsyncSession) -> None:
    await _notify(
        session,
        event.student_id,
        NotificationType.APPLICATION,
        "Application updated",
        f"Your application moved to {event.new_status}." + (f" {event.remarks}" if event.remarks else ""),
    )


async def notify_student_of_document_approval(event: DocumentApproved, session: AsyncSession) -> None:
    await _notify(
        session, event.student_id, NotificationType.DOCUMENT, "Document approved", f"{event.title} was approved."
    )


async def notify_student_of_document_rejection(event: DocumentRejected, session: AsyncSession) -> None:
    await _notify(
        session,
        event.student_id,
        NotificationType.DOCUMENT,
        "Document needs attention",
        f"{event.title} was rejected: {event.reason}",
    )


# --- Application checklist sync (document request fulfilment) -----------------

# A student fulfils a requested `ApplicationChecklistItem` by uploading a
# `Document` and linking it (`document_id`) — but verifying/rejecting that
# upload happens on the `Document` itself via the shared /documents/{id}/verify
# route, not on the checklist item. Without this, a verified document would
# leave its checklist item stuck on SUBMITTED forever.


async def sync_checklist_item_on_document_approval(event: DocumentApproved, session: AsyncSession) -> None:
    await session.execute(
        update(ApplicationChecklistItem)
        .where(ApplicationChecklistItem.document_id == event.document_id)
        .values(status=ChecklistItemStatus.VERIFIED)
    )
    await session.commit()


async def sync_checklist_item_on_document_rejection(event: DocumentRejected, session: AsyncSession) -> None:
    await session.execute(
        update(ApplicationChecklistItem)
        .where(ApplicationChecklistItem.document_id == event.document_id)
        .values(status=ChecklistItemStatus.REJECTED)
    )
    await session.commit()


async def notify_assignee_of_task(event: TaskAssigned, session: AsyncSession) -> None:
    await _notify(session, event.assigned_to, NotificationType.TASK, "New task assigned", event.title)


async def notify_student_of_payment(event: PaymentCompleted, session: AsyncSession) -> None:
    await _notify(
        session,
        event.student_id,
        NotificationType.PAYMENT,
        "Payment received",
        f"We received your payment of {event.amount:.2f}.",
    )


async def notify_reviewers_of_upload(event: DocumentUploaded, session: AsyncSession) -> None:
    """Tell whoever is responsible for this student that a document is waiting.

    Only when the student uploaded it themselves — staff uploading on a
    student's behalf do not need telling.
    """
    if event.uploaded_by != event.student_id:
        return
    staff_ids = await resolve_responsible_staff_ids(session, event.student_id)
    for staff_id in staff_ids:
        session.add(
            Notification(
                user_id=staff_id,
                type=NotificationType.DOCUMENT,
                title="New document uploaded",
                message=f"{event.title} was uploaded for review",
            )
        )
    if staff_ids:
        await session.commit()


async def notify_about_appointment(event: AppointmentScheduled, session: AsyncSession) -> None:
    """Who hears about a new appointment depends on who made it.

    A student requesting a slot is news for the staff responsible for them; a
    counsellor booking one is news for the student. ED360 only did the first
    half, so a student was never told when staff scheduled them.
    """
    if event.student_id is None:
        return

    if event.requested_by_student:
        for staff_id in await resolve_responsible_staff_ids(session, event.student_id):
            session.add(
                Notification(
                    user_id=staff_id,
                    type=NotificationType.APPOINTMENT,
                    title="New appointment request",
                    message=f"A student requested an appointment: {event.title}",
                )
            )
        await session.commit()
        return

    await _notify(
        session,
        event.student_id,
        NotificationType.APPOINTMENT,
        "Appointment scheduled",
        f"An appointment has been scheduled for you: {event.title}",
    )


# --- Progress and points (Phase 6) -----------------------------------------------

# The plan's rule: progress and points advance from events only. Nothing the
# student can call moves either, which is why there is no "award points"
# endpoint anywhere in the API.


async def advance_progress_on_document(event: DocumentApproved, session: AsyncSession) -> None:
    await ProgressService(session).complete_milestone(event.student_id, "documents", source=event.name)


async def advance_progress_on_status(event: ApplicationStatusChanged, session: AsyncSession) -> None:
    """Map application statuses onto the journey ladder.

    Statuses that do not correspond to a milestone simply do nothing — the map
    is deliberately partial rather than asserting over every enum member.
    """
    milestone_by_status = {
        "submitted": "apply",
        "offer_received": "offer",
        "visa_approved": "visa",
        "enrolled": "departure",
    }
    key = milestone_by_status.get(event.new_status)
    if key:
        await ProgressService(session).complete_milestone(event.student_id, key, source=event.name)


async def award_points_for_document(event: DocumentApproved, session: AsyncSession) -> None:
    await PointsService(session).award(
        event.student_id, "document.upload", reference_id=event.document_id, description=event.title
    )


async def award_points_for_application(event: ApplicationStatusChanged, session: AsyncSession) -> None:
    if event.new_status == "submitted":
        await PointsService(session).award(event.student_id, "application.submit", reference_id=event.application_id)


async def advance_progress_on_interview(event: InterviewCompleted, session: AsyncSession) -> None:
    """Reaching an interview completes the milestone regardless of score —
    same as offer/visa/departure, which mark the stage reached, not passed."""
    await ProgressService(session).complete_milestone(event.student_id, "interview", source=event.name)


async def award_points_for_interview(event: InterviewCompleted, session: AsyncSession) -> None:
    await PointsService(session).award(event.student_id, "interview.complete", reference_id=event.session_id)


async def award_points_for_checklist_item(event: ChecklistItemCompleted, session: AsyncSession) -> None:
    """Pay for a completed journey item — once per item, forever.

    The award is keyed to the item's id, so un-ticking and re-ticking the same
    row cannot farm points. That is the whole reason completion emits an event
    instead of the checklist service awarding directly: the student writes the
    checklist, but never the ledger.
    """
    await PointsService(session).award(
        event.student_id, "task.complete", reference_id=event.item_id, description=event.title
    )


# --- Activity log ---------------------------------------------------------------


async def log_status_change(event: ApplicationStatusChanged, session: AsyncSession) -> None:
    await _log(
        session,
        ActivityType.UPDATE,
        "application",
        event.application_id,
        f"Application status changed from {event.old_status} to {event.new_status}",
        user_id=event.changed_by,
    )


async def log_document_upload(event: DocumentUploaded, session: AsyncSession) -> None:
    await _log(
        session,
        ActivityType.CREATE,
        "document",
        event.document_id,
        f"Document uploaded: {event.title}",
        user_id=event.uploaded_by,
    )


# --- Dashboard cache (Phase 6) ----------------------------------------------

# The dashboard fans out over seven tables and is Redis-cached; every event
# that could change what it shows invalidates that cache rather than waiting
# on the TTL, so a student sees the consequence of a staff action immediately.


async def invalidate_dashboard_on_application_status_changed(
    event: ApplicationStatusChanged, session: AsyncSession
) -> None:
    await invalidate_dashboard_cache(event.student_id)


async def invalidate_dashboard_on_document_event(
    event: DocumentApproved | DocumentRejected | DocumentUploaded, session: AsyncSession
) -> None:
    await invalidate_dashboard_cache(event.student_id)


async def invalidate_dashboard_on_appointment_scheduled(event: AppointmentScheduled, session: AsyncSession) -> None:
    if event.student_id is not None:
        await invalidate_dashboard_cache(event.student_id)


async def invalidate_dashboard_on_payment_completed(event: PaymentCompleted, session: AsyncSession) -> None:
    await invalidate_dashboard_cache(event.student_id)


async def invalidate_dashboard_on_checklist_item_completed(
    event: ChecklistItemCompleted, session: AsyncSession
) -> None:
    await invalidate_dashboard_cache(event.student_id)


async def invalidate_dashboard_on_interview_completed(event: InterviewCompleted, session: AsyncSession) -> None:
    await invalidate_dashboard_cache(event.student_id)


def register_subscribers() -> None:
    """Wire every subscriber. Idempotent — safe to call more than once.

    Called from `app.main` at import time so the bus is populated before any
    request runs.
    """
    if event_bus.handler_count(ApplicationStatusChanged) > 0:
        return

    event_bus.subscribe(ApplicationStatusChanged, notify_student_of_status_change)
    event_bus.subscribe(ApplicationStatusChanged, log_status_change)
    event_bus.subscribe(DocumentApproved, notify_student_of_document_approval)
    event_bus.subscribe(DocumentRejected, notify_student_of_document_rejection)
    event_bus.subscribe(DocumentApproved, sync_checklist_item_on_document_approval)
    event_bus.subscribe(DocumentRejected, sync_checklist_item_on_document_rejection)
    event_bus.subscribe(DocumentUploaded, log_document_upload)
    event_bus.subscribe(DocumentUploaded, notify_reviewers_of_upload)
    event_bus.subscribe(AppointmentScheduled, notify_about_appointment)

    # Phase 6 modules ride the same events.
    event_bus.subscribe(DocumentApproved, advance_progress_on_document)
    event_bus.subscribe(DocumentApproved, award_points_for_document)
    event_bus.subscribe(ApplicationStatusChanged, advance_progress_on_status)
    event_bus.subscribe(ApplicationStatusChanged, award_points_for_application)
    event_bus.subscribe(ChecklistItemCompleted, award_points_for_checklist_item)
    event_bus.subscribe(InterviewCompleted, advance_progress_on_interview)
    event_bus.subscribe(InterviewCompleted, award_points_for_interview)
    event_bus.subscribe(TaskAssigned, notify_assignee_of_task)
    event_bus.subscribe(PaymentCompleted, notify_student_of_payment)

    event_bus.subscribe(ApplicationStatusChanged, invalidate_dashboard_on_application_status_changed)
    event_bus.subscribe(DocumentApproved, invalidate_dashboard_on_document_event)
    event_bus.subscribe(DocumentRejected, invalidate_dashboard_on_document_event)
    event_bus.subscribe(DocumentUploaded, invalidate_dashboard_on_document_event)
    event_bus.subscribe(AppointmentScheduled, invalidate_dashboard_on_appointment_scheduled)
    event_bus.subscribe(PaymentCompleted, invalidate_dashboard_on_payment_completed)
    event_bus.subscribe(ChecklistItemCompleted, invalidate_dashboard_on_checklist_item_completed)
    event_bus.subscribe(InterviewCompleted, invalidate_dashboard_on_interview_completed)

    logger.info("Event subscribers registered")


__all__ = ["Event", "register_subscribers"]
