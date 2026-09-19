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

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ActivityLog, ApplicationChecklistItem, Lead, LeadActivity, Notification, User
from ..models.enums import (
    ActivityType,
    ChecklistItemStatus,
    ConversionSource,
    LeadActivityType,
    LeadSource,
    LeadStatus,
    NotificationType,
    UserRole,
    UserStatus,
)
from ..services.progress_service import PointsService, ProgressService
from ..services.staff_resolution import resolve_responsible_staff_ids
from .cache import invalidate_dashboard_cache
from .events import (
    ApplicationStatusChanged,
    ApplicationSubmitted,
    AppointmentScheduled,
    ChecklistItemCompleted,
    DocumentApproved,
    DocumentRejected,
    DocumentUploaded,
    Event,
    InterviewCompleted,
    MilestoneRecorded,
    PaymentCompleted,
    StudentCreated,
    TaskAssigned,
    ThreadMessagePosted,
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


# --- Correspondence -----------------------------------------------------------


async def notify_about_thread_message(event: ThreadMessagePosted, session: AsyncSession) -> None:
    """Tell whichever side did not write it.

    A student's message goes to the staff responsible for them; a staff reply
    goes to the student. The asymmetry lives here rather than in
    `CommunicationService` because it is a question about who cares, not about
    what happened — the service's job ends at recording the message.

    A thread that is still only against a lead has no `student_id` and so no
    portal account to notify. Staff still hear about it.
    """
    if event.is_from_student:
        if event.student_id is None:
            return
        staff_ids = await resolve_responsible_staff_ids(session, event.student_id)
        for staff_id in staff_ids:
            session.add(
                Notification(
                    user_id=staff_id,
                    type=NotificationType.MESSAGE,
                    title=f"Reply: {event.subject}",
                    message=event.preview,
                    related_type="thread",
                    related_id=event.thread_id,
                    action_url=f"/communication?thread={event.thread_id}",
                )
            )
        if staff_ids:
            await session.commit()
        return

    if event.student_id is None:
        return
    session.add(
        Notification(
            user_id=event.student_id,
            type=NotificationType.MESSAGE,
            title=event.subject,
            message=event.preview,
            related_type="thread",
            related_id=event.thread_id,
            action_url=f"/messages?thread={event.thread_id}",
        )
    )
    await session.commit()


# --- Applications -------------------------------------------------------------


async def notify_staff_of_new_application(event: ApplicationSubmitted, session: AsyncSession) -> None:
    """The notification that did not exist.

    Documents raised notifications, status changes notified the student, and an
    application *arriving* notified nobody — staff found out by refreshing a
    list. This is the first thing the brief asks for and the one with the
    clearest cost when it is missing: an application nobody has looked at.

    Sent to the staff responsible for the student where there are any, and to
    every admin/manager otherwise — an unassigned application is precisely the
    one most likely to be missed, so it must not notify an empty set.
    """
    recipients = await resolve_responsible_staff_ids(session, event.student_id)
    if not recipients:
        recipients = list(
            (
                await session.scalars(
                    select(User.id).where(
                        User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.MANAGER]),
                        User.deleted_at.is_(None),
                    )
                )
            ).all()
        )

    university = f" — {event.university_name}" if event.university_name else ""
    for user_id in recipients:
        session.add(
            Notification(
                user_id=user_id,
                type=NotificationType.APPLICATION,
                title="New application submitted",
                message=f"{event.student_name} submitted an application for {event.program_name}{university}.",
                related_type="application",
                related_id=event.application_id,
                action_url=f"/applications/{event.application_id}",
            )
        )
    if recipients:
        await session.commit()


async def notify_student_of_milestone(event: MilestoneRecorded, session: AsyncSession) -> None:
    """The good news, as a notification that outlives the celebration.

    The dashboard shows a one-time overlay (see `ApplicationMilestone.seen_at`);
    this is the durable record of it, so a student who dismissed the overlay can
    still find the offer afterwards.
    """
    university = event.university_name or "the university"
    titles = {
        "offer_received": "🎉 Offer received",
        "cas_received": "Your CAS has arrived",
        "visa_approved": "Visa approved",
    }
    messages = {
        "offer_received": f"{university} has made you an offer for {event.program_name}.",
        "cas_received": f"{university} has issued your CAS for {event.program_name}.",
        "visa_approved": f"Your visa for {event.program_name} has been approved.",
    }
    session.add(
        Notification(
            user_id=event.student_id,
            type=NotificationType.APPLICATION,
            title=titles.get(event.kind, "Application update"),
            message=messages.get(event.kind, f"Your application for {event.program_name} was updated."),
            related_type="application",
            related_id=event.application_id,
            action_url=f"/applications/{event.application_id}",
        )
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
        # `enrolled` used to complete "departure"; the journey now ends at the
        # visa, so enrolment has no milestone of its own.
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
    same as offer/visa, which mark the stage reached, not passed."""
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


# --- Lead coverage ------------------------------------------------------------


#: Who hears about a portal self-registration.
#:
#: Not `resolve_responsible_staff_ids`: that answers "who owns this student",
#: and a student who just signed up is owned by nobody — it would fall through
#: to admins only. A fresh registration is a new lead on the desk, so it goes to
#: everyone who works the desk. Managers and admins are included because they
#: are the ones who notice when nobody picks it up.
LEAD_DESK_ROLES = (
    UserRole.COUNSELLOR.value,
    UserRole.ADMISSIONS.value,
    UserRole.FRONTDESK.value,
    UserRole.MANAGER.value,
    UserRole.ADMIN.value,
    UserRole.SUPER_ADMIN.value,
)


async def _lead_desk_staff_ids(session: AsyncSession) -> list[UUID]:
    result = await session.execute(
        select(User.id).where(
            User.role.in_(LEAD_DESK_ROLES),
            User.status == UserStatus.ACTIVE.value,
            User.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def notify_desk_of_registration(session: AsyncSession, user: User, *, linked: bool) -> None:
    """"Somebody just registered" — said once, to the people who act on it.

    The lead row this accompanies has existed since Phase 5b, but nothing
    announced it: a student could sign up on the portal and sit unworked in the
    pipeline until someone happened to sort by created date. The message names
    the person and says which of the two things happened, because they need
    different responses — a brand-new registration is a first contact to make,
    while a registration against an existing lead means someone already in the
    pipeline has just self-served and should not be cold-called about it.
    """
    name = f"{user.first_name} {user.last_name or ''}".strip() or user.email
    title = "New student registration"
    message = (
        f"{name} registered on the student portal and matched an existing lead."
        if linked
        else f"{name} registered on the student portal and is now a lead."
    )
    for staff_id in await _lead_desk_staff_ids(session):
        session.add(
            Notification(
                user_id=staff_id,
                type=NotificationType.LEAD,
                title=title,
                message=message,
            )
        )
    await session.commit()


#: Shortest string `LeadBase.phone` will accept. Kept next to its one caller
#: rather than imported, because the point is that the two must agree and a
#: mismatch here is a broken leads list rather than a broken lead.
_MIN_LEAD_PHONE = 7


def _usable_phone(phone: str | None) -> str:
    """A number worth storing, or an honest admission that there is none."""
    cleaned = (phone or "").strip()
    return cleaned if len(cleaned) >= _MIN_LEAD_PHONE else "not provided"


async def link_or_create_lead_for_student(event: StudentCreated, session: AsyncSession) -> None:
    """Make sure every student account has a lead behind it.

    The admin console works one pipeline: raw lead -> prospect -> client. A student
    who signs up on the public portal never passed through it, so before this
    existed they were reachable only through Users & Staff, which is admin-only —
    a counsellor answering their call could not open them at all.

    **Link before insert.** `leads.email` carries a unique index where the address
    is not null, so an insert for an address a lead already holds would fail. It
    would also be wrong: someone who filled in the eligibility form and *then*
    registered is one person, and this joins the two records rather than making a
    second one.

    Conversion here is a statement of fact, not of sales progress — they already
    have an account — so it records `registration_completed` as the source.
    """
    result = await session.execute(select(Lead).where(Lead.email == event.email))
    lead = result.scalar_one_or_none()

    if lead is not None:
        if lead.converted_user_id is not None:
            return
        old_status = lead.status
        lead.converted_user_id = event.student_id
        lead.status = LeadStatus.CONVERTED
        lead.converted_at = event.occurred_at
        if lead.conversion_source is None:
            lead.conversion_source = ConversionSource.REGISTRATION_COMPLETED
        session.add(
            LeadActivity(
                lead_id=lead.id,
                activity_type=LeadActivityType.CONVERTED,
                title="Registered on the portal",
                description="They created their own student account, so this lead is now a client.",
                old_status=old_status,
                new_status=LeadStatus.CONVERTED,
            )
        )
        await session.commit()
        logger.info("Linked lead %s to self-registered student %s", lead.id, event.student_id)

        user = (await session.execute(select(User).where(User.id == event.student_id))).scalar_one_or_none()
        if user is not None:
            await notify_desk_of_registration(session, user, linked=True)
        return

    user = (await session.execute(select(User).where(User.id == event.student_id))).scalar_one_or_none()
    if user is None:  # pragma: no cover - the row was just committed by the caller
        return

    new_lead = Lead(
        first_name=user.first_name,
        last_name=user.last_name or None,
        email=user.email,
        # `leads.phone` is NOT NULL, and `LeadBase` wants at least seven
        # characters. Self-signup does not require a number at all, and until
        # `PublicRegisterRequest` gained a minimum it could supply a useless
        # one — so this guards on length, not merely on blank. A lead the
        # console cannot list is worse than a lead with no number, and saying
        # "not provided" is better than inventing one.
        phone=_usable_phone(user.phone),
        source=LeadSource.WEBSITE,
        status=LeadStatus.CONVERTED,
        converted_user_id=user.id,
        converted_at=event.occurred_at,
        conversion_source=ConversionSource.REGISTRATION_COMPLETED,
    )
    session.add(new_lead)
    await session.flush()
    session.add(
        LeadActivity(
            lead_id=new_lead.id,
            activity_type=LeadActivityType.LEAD_CREATED,
            title="Signed up on the portal",
            description="Created automatically so the student is reachable from the Leads pipeline.",
            new_status=LeadStatus.CONVERTED,
        )
    )
    await session.commit()
    logger.info("Created lead %s for self-registered student %s", new_lead.id, event.student_id)
    await notify_desk_of_registration(session, user, linked=False)


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
    event_bus.subscribe(ThreadMessagePosted, notify_about_thread_message)
    event_bus.subscribe(ApplicationSubmitted, notify_staff_of_new_application)
    event_bus.subscribe(MilestoneRecorded, notify_student_of_milestone)
    event_bus.subscribe(DocumentUploaded, notify_reviewers_of_upload)
    event_bus.subscribe(AppointmentScheduled, notify_about_appointment)
    event_bus.subscribe(StudentCreated, link_or_create_lead_for_student)

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
