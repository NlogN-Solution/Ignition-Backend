"""The application journey, for the student portal and the staff console.

One set of URLs under `/applications/{id}/journey`, like the workflow and
checklist routes beside it: `assert_application_access` decides who reaches the
application at all (owner student, or staff under the own-work rule, 404
otherwise), and each action then says which side may take it. Every action
answers with the whole journey, so neither screen has to reconcile a partial
response with what it already drew.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.application_access import APPLICATION_STAFF_ROLES, assert_application_access
from ..api.auth import get_current_user, require_role
from ..api.deps import get_db_session
from ..api.exceptions import ForbiddenException
from ..models import Application, User
from ..models.enums import UserRole
from ..schemas.journey import (
    JourneyRead,
    SlotOutcome,
    SlotsPublish,
    StudyGapUpdate,
    SubmissionCreate,
    SubmissionReview,
    SwitchTemplateRequest,
    TaskTick,
)
from ..services.application_service import ApplicationService, get_application_service
from ..services.journey_service import JourneyService

router = APIRouter(prefix="/applications/{application_id}/journey", tags=["Journey"])


async def get_journey_service(session: AsyncSession = Depends(get_db_session)) -> JourneyService:
    return JourneyService(session)


def _is_staff(user: User) -> bool:
    return user.role in APPLICATION_STAFF_ROLES


def _staff_only(user: User) -> None:
    if not _is_staff(user):
        raise ForbiddenException("Only your counsellor can do that")


def _student_only(user: User) -> None:
    if user.role is not UserRole.STUDENT:
        raise ForbiddenException("Only the student can do that")


async def _application(application_id: UUID, user: User, application_service: ApplicationService) -> Application:
    return await assert_application_access(application_id, user, application_service)


async def _view(service: JourneyService, application: Application, user: User) -> JourneyRead:
    await service.session.refresh(application)
    return await service.view(application, for_staff=_is_staff(user))


@router.get("", response_model=JourneyRead, summary="The application's journey")
async def get_journey(
    application_id: UUID,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    application = await _application(application_id, user, application_service)
    return await _view(service, application, user)


@router.put("/study-gap", response_model=JourneyRead, summary="Answer the study-gap question")
async def set_study_gap(
    application_id: UUID,
    payload: StudyGapUpdate,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    application = await _application(application_id, user, application_service)
    await service.set_study_gap(application, payload.has_study_gap, user)
    return await _view(service, application, user)


@router.post("/steps/{step_id}/submit", response_model=JourneyRead, summary="Hand in a documents stage")
async def submit_documents_stage(
    application_id: UUID,
    step_id: UUID,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    application = await _application(application_id, user, application_service)
    step = await service.step_for(application, step_id)
    await service.submit_documents(application, step, user)
    return await _view(service, application, user)


@router.post("/steps/{step_id}/submissions", response_model=JourneyRead, summary="Hand in a review round")
async def create_submission(
    application_id: UUID,
    step_id: UUID,
    payload: SubmissionCreate,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    _student_only(user)
    application = await _application(application_id, user, application_service)
    step = await service.step_for(application, step_id)
    await service.submit_review(application, step, payload, user)
    return await _view(service, application, user)


@router.post("/submissions/{submission_id}/review", response_model=JourneyRead, summary="Verify or send back")
async def review_submission(
    application_id: UUID,
    submission_id: UUID,
    payload: SubmissionReview,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    _staff_only(user)
    application = await _application(application_id, user, application_service)
    submission = await service.submission_for(application, submission_id)
    await service.review_submission(
        application,
        submission,
        verdict=payload.verdict,
        feedback=payload.feedback,
        attachment_ids=payload.attachment_ids,
        user=user,
    )
    return await _view(service, application, user)


@router.post("/steps/{step_id}/slots", response_model=JourneyRead, summary="Offer interview slots")
async def publish_slots(
    application_id: UUID,
    step_id: UUID,
    payload: SlotsPublish,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    _staff_only(user)
    application = await _application(application_id, user, application_service)
    step = await service.step_for(application, step_id)
    await service.publish_slots(application, step, payload.slots, user)
    return await _view(service, application, user)


@router.delete("/slots/{slot_id}", response_model=JourneyRead, summary="Withdraw an unbooked slot")
async def withdraw_slot(
    application_id: UUID,
    slot_id: UUID,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    _staff_only(user)
    application = await _application(application_id, user, application_service)
    slot = await service.slot_for(application, slot_id)
    await service.withdraw_slot(application, slot, user)
    return await _view(service, application, user)


@router.post("/slots/{slot_id}/book", response_model=JourneyRead, summary="Book an interview slot")
async def book_slot(
    application_id: UUID,
    slot_id: UUID,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    _student_only(user)
    application = await _application(application_id, user, application_service)
    slot = await service.slot_for(application, slot_id)
    await service.book_slot(application, slot, user)
    return await _view(service, application, user)


@router.post("/slots/{slot_id}/outcome", response_model=JourneyRead, summary="Record an interview outcome")
async def record_outcome(
    application_id: UUID,
    slot_id: UUID,
    payload: SlotOutcome,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    _staff_only(user)
    application = await _application(application_id, user, application_service)
    slot = await service.slot_for(application, slot_id)
    await service.record_outcome(application, slot, payload.outcome, payload.note, user)
    return await _view(service, application, user)


@router.put("/steps/{step_id}/tasks/{task_key}", response_model=JourneyRead, summary="Tick a checklist task")
async def tick_task(
    application_id: UUID,
    step_id: UUID,
    task_key: str,
    payload: TaskTick,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> JourneyRead:
    application = await _application(application_id, user, application_service)
    step = await service.step_for(application, step_id)
    await service.tick_task(application, step, task_key, payload.done, user)
    return await _view(service, application, user)


@router.post("/switch-template", response_model=JourneyRead, summary="Move the application to another journey")
async def switch_template(
    application_id: UUID,
    payload: SwitchTemplateRequest,
    service: JourneyService = Depends(get_journey_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> JourneyRead:
    application = await _application(application_id, user, application_service)
    await service.switch_template(application, payload.template_id, user)
    return await _view(service, application, user)
