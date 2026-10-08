"""The application journey: what each workflow stage asks, and who moves it.

## What this adds to the workflow engine

`ApplicationWorkflowService` knows steps and their statuses. It does not know
what a student *does* at a step. This service reads each stage's `kind` and
`config` (see `WorkflowStageKind`) and turns them into actions:

=============  ==============================  ==============================
kind           student                         staff
=============  ==============================  ==============================
documents      upload checklist items; submit  verify documents (existing flow)
issued         wait                            record the offer/CAS milestone
review         hand in answers / a recording   verify, or send back with notes
booking        book one of the offered slots   publish slots; record outcome
checklist      tick the tasks                  –
info           –                               move the step by hand
=============  ==============================  ==============================

Config keys, by kind:

- every kind: `on_complete_status` — the application status completing the step
  moves the application to (forward only); `on_fail_status` — likewise on failure.
- documents: `level_aware` — the stage whose items depend on UG/PG/gap.
- issued: `milestone_status` — the status whose recording completes the step.
- review: `resources` (list of {title, description, url}), `allow_text`,
  `allow_document`, `allow_link`, `accept` (`document` | `video`),
  `requires_practice` — interview type keys the student must have completed a
  practice session of before handing anything in.
- booking: `allow_reschedule`, `fail_ends_journey`, `appointment_type`.
- checklist: `tasks` (list of {key, label}).

## Rules every action keeps

- **Only the current step moves.** Acting on a step that is not `current` is a
  409: a student cannot hand in interview answers before the offer arrives,
  and a counsellor cannot verify a round that has been superseded.
- **Completion goes through `ApplicationWorkflowService.update_step`**, so the
  activity log, timestamps and advancing to the next step stay in one place.
- **Status moves forward only.** A journey step never drags an application
  backwards: completing the application stage of an application that already
  has an offer leaves the offer alone. Failure statuses are the exception —
  a failed suitability interview is the end, wherever the status stood.
- **Statuses drive issued stages, never the reverse.** Recording an offer is
  the existing milestone dialog; `sync_journey_on_status_change` completes the
  step it unlocks. There is no second "issue offer" button to drift from it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes, selectinload

from ..api.exceptions import BadRequestException, ConflictException, NotFoundException
from ..models import (
    Application,
    ApplicationChecklistItem,
    ApplicationWorkflow,
    ApplicationWorkflowStep,
    Appointment,
    Document,
    InterviewSession,
    InterviewType,
    Program,
    User,
    WorkflowStage,
    WorkflowStepActivity,
    WorkflowStepSlot,
    WorkflowStepSubmission,
    WorkflowTemplate,
)
from ..models.enums import (
    ApplicationStatus,
    ApplicationWorkflowStatus,
    AppointmentStatus,
    AppointmentType,
    ChecklistItemStatus,
    InterviewSessionStatus,
    NotificationType,
    StepSlotOutcome,
    StepSlotStatus,
    StepSubmissionStatus,
    WorkflowActivityType,
    WorkflowStageKind,
    WorkflowStepStatus,
)
from ..schemas.journey import (
    JourneyChecklistItemRead,
    JourneyRead,
    JourneySlotRead,
    JourneyStepRead,
    JourneySubmissionRead,
    SlotCreate,
    SubmissionCreate,
)
from .application_service import ApplicationService
from .notification_service import NotificationService
from .staff_resolution import resolve_responsible_staff_ids
from .workflow_service import ApplicationWorkflowService, study_level_of

#: The order an application's status normally advances in. Statuses missing
#: from it (declined, refused, withdrawn, rejected) are endings; the journey
#: never moves an application *out* of one by itself.
_FORWARD_ORDER: list[ApplicationStatus] = [
    ApplicationStatus.REQUESTED,
    ApplicationStatus.DRAFT,
    ApplicationStatus.DOCUMENTS_PENDING,
    ApplicationStatus.READY_TO_SUBMIT,
    ApplicationStatus.SUBMITTED,
    ApplicationStatus.UNDER_REVIEW,
    ApplicationStatus.OFFER_RECEIVED,
    ApplicationStatus.OFFER_ACCEPTED,
    ApplicationStatus.CAS_RECEIVED,
    ApplicationStatus.VISA_PROCESSING,
    ApplicationStatus.VISA_APPROVED,
    ApplicationStatus.ENROLLED,
]
_RANK = {status: index for index, status in enumerate(_FORWARD_ORDER)}

_TERMINAL_STEP_STATUSES = frozenset(
    {
        WorkflowStepStatus.COMPLETED,
        WorkflowStepStatus.FAILED,
        WorkflowStepStatus.SKIPPED,
        WorkflowStepStatus.CANCELLED,
    }
)

#: A checklist item that no longer blocks its stage.
_SETTLED_ITEM_STATUSES = frozenset(
    {ChecklistItemStatus.SUBMITTED, ChecklistItemStatus.VERIFIED, ChecklistItemStatus.WAIVED}
)


def _kind(stage: WorkflowStage | None) -> WorkflowStageKind:
    try:
        return WorkflowStageKind(stage.kind) if stage is not None else WorkflowStageKind.INFO
    except ValueError:
        return WorkflowStageKind.INFO


def _config(stage: WorkflowStage | None) -> dict[str, Any]:
    return dict(stage.config or {}) if stage is not None else {}


def _status_from_config(value: Any) -> ApplicationStatus | None:
    try:
        return ApplicationStatus(value) if value else None
    except ValueError:
        return None


def _linked_status(stage: WorkflowStage | None) -> ApplicationStatus | None:
    """The status a step stands for: the one it waits on, or the one it sets."""
    config = _config(stage)
    return _status_from_config(config.get("milestone_status")) or _status_from_config(config.get("on_complete_status"))


class JourneyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workflows = ApplicationWorkflowService(session)

    # ------------------------------------------------------------------ loading

    async def _workflow(self, application_id: UUID) -> ApplicationWorkflow | None:
        return await self.session.scalar(
            select(ApplicationWorkflow)
            .where(ApplicationWorkflow.application_id == application_id)
            .options(
                selectinload(ApplicationWorkflow.template),
                selectinload(ApplicationWorkflow.steps)
                .selectinload(ApplicationWorkflowStep.stage)
                .selectinload(WorkflowStage.document_requirements),
            )
            .execution_options(populate_existing=True)
        )

    async def _workflow_or_404(self, application_id: UUID) -> ApplicationWorkflow:
        workflow = await self._workflow(application_id)
        if workflow is None:
            raise NotFoundException("This application has no journey yet")
        return workflow

    async def step_for(self, application: Application, step_id: UUID) -> ApplicationWorkflowStep:
        """A step of *this* application's journey, or 404 (FAPI-SEC-003)."""
        workflow = await self._workflow_or_404(application.id)
        step = next((s for s in workflow.steps if s.id == step_id), None)
        if step is None:
            raise NotFoundException("Journey step not found")
        return step

    async def submission_for(self, application: Application, submission_id: UUID) -> WorkflowStepSubmission:
        submission = await self.session.scalar(
            select(WorkflowStepSubmission)
            .join(ApplicationWorkflowStep, ApplicationWorkflowStep.id == WorkflowStepSubmission.step_id)
            .join(ApplicationWorkflow, ApplicationWorkflow.id == ApplicationWorkflowStep.application_workflow_id)
            .where(WorkflowStepSubmission.id == submission_id, ApplicationWorkflow.application_id == application.id)
        )
        if submission is None:
            raise NotFoundException("Submission not found")
        return submission

    async def slot_for(self, application: Application, slot_id: UUID) -> WorkflowStepSlot:
        slot = await self.session.scalar(
            select(WorkflowStepSlot)
            .join(ApplicationWorkflowStep, ApplicationWorkflowStep.id == WorkflowStepSlot.step_id)
            .join(ApplicationWorkflow, ApplicationWorkflow.id == ApplicationWorkflowStep.application_workflow_id)
            .where(WorkflowStepSlot.id == slot_id, ApplicationWorkflow.application_id == application.id)
        )
        if slot is None:
            raise NotFoundException("Slot not found")
        return slot

    async def _step_with_stage(self, step_id: UUID) -> ApplicationWorkflowStep:
        step = await self.session.scalar(
            select(ApplicationWorkflowStep)
            .where(ApplicationWorkflowStep.id == step_id)
            .options(selectinload(ApplicationWorkflowStep.stage))
            .execution_options(populate_existing=True)
        )
        assert step is not None
        return step

    # --------------------------------------------------------------------- view

    async def view(self, application: Application, *, for_staff: bool) -> JourneyRead:
        workflow = await self._workflow_or_404(application.id)
        step_ids = [step.id for step in workflow.steps]

        items = (
            await self.session.scalars(
                select(ApplicationChecklistItem)
                .where(ApplicationChecklistItem.application_id == application.id)
                .order_by(ApplicationChecklistItem.created_at)
            )
        ).all()
        submissions = (
            await self.session.scalars(
                select(WorkflowStepSubmission)
                .where(WorkflowStepSubmission.step_id.in_(step_ids))
                .options(selectinload(WorkflowStepSubmission.document))
                .order_by(WorkflowStepSubmission.round)
            )
        ).all()
        slots = (
            await self.session.scalars(
                select(WorkflowStepSlot)
                .where(WorkflowStepSlot.step_id.in_(step_ids))
                .order_by(WorkflowStepSlot.attempt, WorkflowStepSlot.starts_at)
            )
        ).all()

        program = await self.session.get(Program, application.program_id)
        steps: list[JourneyStepRead] = []
        for step in workflow.steps:
            stage = step.stage
            kind = _kind(stage)
            step_subs = [s for s in submissions if s.step_id == step.id]
            step_slots = [s for s in slots if s.step_id == step.id]
            if not for_staff:
                step_slots = [s for s in step_slots if s.status is not StepSlotStatus.WITHDRAWN]
            steps.append(
                JourneyStepRead(
                    id=step.id,
                    stage_id=step.stage_id,
                    key=stage.key if stage else None,
                    name=step.stage_name_snapshot,
                    description=stage.description if stage else None,
                    kind=kind,
                    config=_config(stage),
                    status=step.status,
                    order=step.order,
                    started_at=step.started_at,
                    completed_at=step.completed_at,
                    progress=dict(step.progress or {}),
                    checklist=[
                        JourneyChecklistItemRead.model_validate(item)
                        for item in items
                        if step.stage_id is not None and item.stage_id == step.stage_id
                    ],
                    submissions=[self._submission_read(s) for s in step_subs],
                    slots=[JourneySlotRead.model_validate(s) for s in step_slots],
                    waiting_on=self._waiting_on(step, kind, step_subs, step_slots),
                    notes=step.notes if for_staff else None,
                    assigned_to=step.assigned_to if for_staff else None,
                )
            )

        # Skipped counts as behind the student: a stage an early offer made moot
        # must not hold the bar short of 100% forever.
        done = sum(
            1 for step in workflow.steps if step.status in (WorkflowStepStatus.COMPLETED, WorkflowStepStatus.SKIPPED)
        )
        current = next((s for s in workflow.steps if s.status is WorkflowStepStatus.CURRENT), None)
        return JourneyRead(
            application_id=application.id,
            workflow_id=workflow.id,
            template_id=workflow.template_id,
            template_name=workflow.template.name if workflow.template else "",
            status=workflow.status.value,
            study_level=study_level_of(program),
            has_study_gap=application.has_study_gap,
            current_step_id=current.id if current else None,
            progress_percent=round(done / len(workflow.steps) * 100) if workflow.steps else 0,
            steps=steps,
        )

    @staticmethod
    def _submission_read(submission: WorkflowStepSubmission) -> JourneySubmissionRead:
        read = JourneySubmissionRead.model_validate(submission)
        if submission.document is not None:
            read.document_name = submission.document.title or submission.document.original_file_name
        return read

    @staticmethod
    def _waiting_on(
        step: ApplicationWorkflowStep,
        kind: WorkflowStageKind,
        submissions: list[WorkflowStepSubmission],
        slots: list[WorkflowStepSlot],
    ) -> str | None:
        if step.status is not WorkflowStepStatus.CURRENT:
            return None
        if kind in (WorkflowStageKind.DOCUMENTS, WorkflowStageKind.CHECKLIST):
            return "student"
        if kind is WorkflowStageKind.REVIEW:
            latest = submissions[-1] if submissions else None
            if latest is not None and latest.status is StepSubmissionStatus.SUBMITTED:
                return "staff"
            return "student"
        if kind is WorkflowStageKind.BOOKING:
            if any(s.status is StepSlotStatus.BOOKED for s in slots):
                return "staff"
            if any(s.status is StepSlotStatus.OPEN for s in slots):
                return "student"
            return "staff"
        return "staff"

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _require(step: ApplicationWorkflowStep, kind: WorkflowStageKind) -> None:
        if _kind(step.stage) is not kind:
            raise BadRequestException("That action does not apply to this stage")
        if step.status is not WorkflowStepStatus.CURRENT:
            raise ConflictException("This stage is not open yet, or it is already finished")

    def _activity(
        self, step: ApplicationWorkflowStep, activity: WorkflowActivityType, user_id: UUID | None, comment: str
    ) -> None:
        self.session.add(
            WorkflowStepActivity(step_id=step.id, activity_type=activity, performed_by=user_id, comment=comment)
        )

    async def _notify_student(self, application: Application, title: str, message: str) -> None:
        await NotificationService(self.session).notify_many(
            [application.student_id], notification_type=NotificationType.APPLICATION, title=title, message=message
        )

    async def _notify_staff(self, application: Application, title: str, message: str) -> None:
        recipients = (
            [application.counsellor_id]
            if application.counsellor_id
            else await resolve_responsible_staff_ids(self.session, application.student_id)
        )
        await NotificationService(self.session).notify_many(
            recipients, notification_type=NotificationType.APPLICATION, title=title, message=message
        )

    async def _move_status(
        self, application: Application, target: ApplicationStatus | None, user_id: UUID | None, remarks: str
    ) -> None:
        """Advance the application's status, never backwards."""
        if target is None:
            return
        await self.session.refresh(application)
        current_rank = _RANK.get(application.status)
        target_rank = _RANK.get(target)
        if current_rank is None or target_rank is None or target_rank <= current_rank:
            return
        await ApplicationService(self.session).change_application_status(
            application, target, performed_by=user_id, remarks=remarks
        )

    async def _complete(self, application: Application, step: ApplicationWorkflowStep, user_id: UUID | None) -> None:
        config = _config(step.stage)
        await self.workflows.update_step(step, {"status": WorkflowStepStatus.COMPLETED}, performed_by=user_id)
        await self._move_status(
            application,
            _status_from_config(config.get("on_complete_status")),
            user_id,
            f"{step.stage_name_snapshot} completed",
        )

    # ------------------------------------------------------- documents stages

    async def set_study_gap(self, application: Application, has_gap: bool, user: User) -> None:
        """The student's gap answer, and the gap item it adds or removes.

        Only while the level-aware documents stage is still open: once the
        application documents are handed in, the answer is evidence and
        changing it is a conversation with the counsellor.
        """
        workflow = await self._workflow_or_404(application.id)
        stage_step = next(
            (
                s
                for s in workflow.steps
                if _kind(s.stage) is WorkflowStageKind.DOCUMENTS and _config(s.stage).get("level_aware")
            ),
            None,
        )
        if stage_step is None or stage_step.stage is None:
            raise BadRequestException("This journey does not ask about study gaps")
        if stage_step.status in _TERMINAL_STEP_STATUSES or stage_step.progress.get("submitted_at"):
            raise ConflictException("Your application documents are already submitted")

        application.has_study_gap = has_gap
        gap_requirements = [r for r in stage_step.stage.document_requirements if r.condition == "gap"]
        for requirement in gap_requirements:
            existing = await self.session.scalar(
                select(ApplicationChecklistItem).where(
                    ApplicationChecklistItem.application_id == application.id,
                    ApplicationChecklistItem.stage_id == stage_step.stage_id,
                    ApplicationChecklistItem.custom_label == requirement.custom_label,
                )
            )
            if has_gap and existing is None:
                self.session.add(
                    ApplicationChecklistItem(
                        application_id=application.id,
                        stage_id=stage_step.stage_id,
                        document_type=requirement.document_type,
                        custom_label=requirement.custom_label,
                        is_required=requirement.is_required,
                    )
                )
            elif has_gap and existing is not None and existing.status is ChecklistItemStatus.WAIVED:
                existing.status = ChecklistItemStatus.SUBMITTED if existing.document_id else ChecklistItemStatus.PENDING
            elif not has_gap and existing is not None:
                if existing.document_id is None:
                    await self.session.delete(existing)
                else:
                    # A file was already uploaded for it: keep the evidence, stop asking.
                    existing.status = ChecklistItemStatus.WAIVED
        await self.session.commit()

    async def submit_documents(self, application: Application, step: ApplicationWorkflowStep, user: User) -> None:
        self._require(step, WorkflowStageKind.DOCUMENTS)
        items = (
            await self.session.scalars(
                select(ApplicationChecklistItem).where(
                    ApplicationChecklistItem.application_id == application.id,
                    ApplicationChecklistItem.stage_id == step.stage_id,
                )
            )
        ).all()
        outstanding = [i for i in items if i.is_required and i.status not in _SETTLED_ITEM_STATUSES]
        if outstanding:
            labels = ", ".join(i.custom_label or "a document" for i in outstanding[:3])
            raise BadRequestException(f"Upload every document first. Still needed: {labels}")

        step.progress = {**(step.progress or {}), "submitted_at": datetime.now(UTC).isoformat()}
        self._activity(step, WorkflowActivityType.SUBMISSION, user.id, "Documents submitted")
        await self.session.commit()
        await self._complete(application, step, user.id)
        await self._notify_staff(
            application,
            f"{step.stage_name_snapshot}: documents submitted",
            "The student has uploaded every document for this stage. Verify them from the application.",
        )

    # ----------------------------------------------------------- review stages

    async def _require_practice(self, application: Application, keys: Any) -> None:
        """Refuse a hand-in until every listed practice interview is finished.

        Only *active* types count: a type retired from the catalogue cannot be
        sat any more, so demanding it would lock the stage for good.
        """
        if not keys:
            return
        required = set(
            await self.session.scalars(
                select(InterviewType.id).where(InterviewType.key.in_(list(keys)), InterviewType.is_active.is_(True))
            )
        )
        if not required:
            return
        done = set(
            await self.session.scalars(
                select(InterviewSession.type_id).where(
                    InterviewSession.student_id == application.student_id,
                    InterviewSession.status == InterviewSessionStatus.COMPLETED,
                    InterviewSession.type_id.in_(required),
                )
            )
        )
        if required - done:
            raise BadRequestException(
                f"Complete all {len(required)} practice interviews first ({len(done)} of {len(required)} done)"
            )

    async def submit_review(
        self, application: Application, step: ApplicationWorkflowStep, payload: SubmissionCreate, user: User
    ) -> WorkflowStepSubmission:
        self._require(step, WorkflowStageKind.REVIEW)
        config = _config(step.stage)
        if payload.body_text and not config.get("allow_text", True):
            raise BadRequestException("This stage takes a file, not typed answers")
        if payload.external_url and not config.get("allow_link", False):
            raise BadRequestException("This stage takes an upload, not a link")
        if payload.document_id and not config.get("allow_document", True):
            raise BadRequestException("This stage does not take a file")
        await self._require_practice(application, config.get("requires_practice"))

        latest = await self.session.scalar(
            select(WorkflowStepSubmission)
            .where(WorkflowStepSubmission.step_id == step.id)
            .order_by(WorkflowStepSubmission.round.desc())
            .limit(1)
        )
        if latest is not None and latest.status is StepSubmissionStatus.SUBMITTED:
            raise ConflictException("Your last submission is still with your counsellor")

        if payload.document_id is not None:
            document = await self.session.get(Document, payload.document_id)
            if document is None or document.student_id != application.student_id:
                raise NotFoundException("Document not found")

        submission = WorkflowStepSubmission(
            step_id=step.id,
            round=(latest.round + 1) if latest else 1,
            submitted_by=user.id,
            body_text=payload.body_text,
            document_id=payload.document_id,
            external_url=payload.external_url,
        )
        self.session.add(submission)
        self._activity(step, WorkflowActivityType.SUBMISSION, user.id, f"Round {submission.round} submitted")
        await self.session.commit()
        await self.session.refresh(submission)
        await self._notify_staff(
            application,
            f"{step.stage_name_snapshot}: ready for review",
            f"Round {submission.round} is waiting for you to verify it or send feedback.",
        )
        return submission

    async def review_submission(
        self,
        application: Application,
        submission: WorkflowStepSubmission,
        *,
        verdict: str,
        feedback: str | None,
        attachment_ids: list[UUID],
        user: User,
    ) -> WorkflowStepSubmission:
        step = await self._step_with_stage(submission.step_id)
        self._require(step, WorkflowStageKind.REVIEW)
        if submission.status is not StepSubmissionStatus.SUBMITTED:
            raise ConflictException("This round has already been reviewed")

        if attachment_ids:
            owned = await self.session.scalar(
                select(func.count(Document.id)).where(
                    Document.id.in_(attachment_ids), Document.student_id == application.student_id
                )
            )
            if owned != len(set(attachment_ids)):
                raise NotFoundException("Document not found")

        submission.status = (
            StepSubmissionStatus.VERIFIED if verdict == "verified" else StepSubmissionStatus.CHANGES_REQUESTED
        )
        submission.feedback = (feedback or "").strip() or None
        submission.feedback_document_ids = list(dict.fromkeys(attachment_ids)) or None
        submission.reviewed_by = user.id
        submission.reviewed_at = datetime.now(UTC)
        self._activity(
            step,
            WorkflowActivityType.REVIEW,
            user.id,
            f"Round {submission.round} {'verified' if verdict == 'verified' else 'sent back'}"
            + (f": {submission.feedback}" if submission.feedback else ""),
        )
        await self.session.commit()

        if verdict == "verified":
            await self._complete(application, step, user.id)
            await self._notify_student(
                application,
                f"{step.stage_name_snapshot} verified",
                "Your counsellor verified your submission. The next stage is open.",
            )
        else:
            await self._notify_student(
                application,
                f"{step.stage_name_snapshot}: feedback from your counsellor",
                f"Your counsellor asked for changes: {submission.feedback}",
            )
        await self.session.refresh(submission)
        return submission

    # ---------------------------------------------------------- booking stages

    async def _current_attempt(self, step: ApplicationWorkflowStep) -> int:
        """The attempt slots are being offered for now.

        A new attempt starts once the last one ended in `reschedule`.
        """
        latest = await self.session.scalar(
            select(WorkflowStepSlot)
            .where(WorkflowStepSlot.step_id == step.id, WorkflowStepSlot.status != StepSlotStatus.WITHDRAWN)
            .order_by(WorkflowStepSlot.attempt.desc(), WorkflowStepSlot.updated_at.desc())
            .limit(1)
        )
        if latest is None:
            last_attempt = await self.session.scalar(
                select(func.max(WorkflowStepSlot.attempt)).where(WorkflowStepSlot.step_id == step.id)
            )
            return last_attempt or 1
        if latest.outcome is StepSlotOutcome.RESCHEDULE:
            return latest.attempt + 1
        return latest.attempt

    async def publish_slots(
        self, application: Application, step: ApplicationWorkflowStep, slots: list[SlotCreate], user: User
    ) -> list[WorkflowStepSlot]:
        self._require(step, WorkflowStageKind.BOOKING)
        attempt = await self._current_attempt(step)
        booked = await self.session.scalar(
            select(WorkflowStepSlot.id).where(
                WorkflowStepSlot.step_id == step.id,
                WorkflowStepSlot.attempt == attempt,
                WorkflowStepSlot.status == StepSlotStatus.BOOKED,
            )
        )
        if booked is not None:
            raise ConflictException("A slot is already booked; record its outcome first")

        now = datetime.now(UTC)
        created = []
        for spec in slots:
            if spec.starts_at.tzinfo is None:
                raise BadRequestException("Slot times must include a timezone")
            if spec.starts_at <= now:
                raise BadRequestException("Slots must be in the future")
            slot = WorkflowStepSlot(
                step_id=step.id,
                attempt=attempt,
                starts_at=spec.starts_at,
                ends_at=spec.ends_at,
                location=spec.location,
                meeting_link=spec.meeting_link,
                created_by=user.id,
            )
            self.session.add(slot)
            created.append(slot)
        self._activity(step, WorkflowActivityType.SLOTS_PUBLISHED, user.id, f"{len(created)} slot(s) offered")
        await self.session.commit()
        await self._notify_student(
            application,
            f"{step.stage_name_snapshot}: pick a time",
            f"{len(created)} interview slot(s) are available. Book one from your application.",
        )
        return created

    async def withdraw_slot(self, application: Application, slot: WorkflowStepSlot, user: User) -> None:
        if slot.status is not StepSlotStatus.OPEN:
            raise ConflictException("Only an unbooked slot can be withdrawn")
        slot.status = StepSlotStatus.WITHDRAWN
        await self.session.commit()

    async def book_slot(self, application: Application, slot: WorkflowStepSlot, user: User) -> WorkflowStepSlot:
        step = await self._step_with_stage(slot.step_id)
        self._require(step, WorkflowStageKind.BOOKING)
        if slot.status is not StepSlotStatus.OPEN:
            raise ConflictException("That slot is no longer available")
        if slot.attempt != await self._current_attempt(step):
            raise ConflictException("That slot is no longer available")
        if slot.starts_at <= datetime.now(UTC):
            raise ConflictException("That slot has already passed")

        config = _config(step.stage)
        try:
            appointment_type = AppointmentType(config.get("appointment_type") or AppointmentType.OTHER.value)
        except ValueError:
            appointment_type = AppointmentType.OTHER
        appointment = Appointment(
            student_id=application.student_id,
            counsellor_id=application.counsellor_id,
            appointment_type=appointment_type,
            status=AppointmentStatus.SCHEDULED,
            title=step.stage_name_snapshot,
            description=f"Booked from the application journey (attempt {slot.attempt}).",
            preferred_date=slot.starts_at.date(),
            start_time=slot.starts_at,
            end_time=slot.ends_at,
            location=slot.location,
            meeting_link=slot.meeting_link,
            created_by=user.id,
        )
        self.session.add(appointment)
        await self.session.flush()

        slot.status = StepSlotStatus.BOOKED
        slot.booked_at = datetime.now(UTC)
        slot.appointment_id = appointment.id
        siblings = (
            await self.session.scalars(
                select(WorkflowStepSlot).where(
                    WorkflowStepSlot.step_id == step.id,
                    WorkflowStepSlot.attempt == slot.attempt,
                    WorkflowStepSlot.status == StepSlotStatus.OPEN,
                    WorkflowStepSlot.id != slot.id,
                )
            )
        ).all()
        for sibling in siblings:
            sibling.status = StepSlotStatus.WITHDRAWN
        self._activity(
            step, WorkflowActivityType.SLOT_BOOKED, user.id, f"Booked {slot.starts_at.isoformat(timespec='minutes')}"
        )
        await self.session.commit()
        await self._notify_staff(
            application,
            f"{step.stage_name_snapshot} booked",
            f"The student booked {slot.starts_at:%d %b %Y, %H:%M} UTC.",
        )
        await self.session.refresh(slot)
        return slot

    async def record_outcome(
        self,
        application: Application,
        slot: WorkflowStepSlot,
        outcome: StepSlotOutcome,
        note: str | None,
        user: User,
    ) -> WorkflowStepSlot:
        step = await self._step_with_stage(slot.step_id)
        self._require(step, WorkflowStageKind.BOOKING)
        if slot.status is not StepSlotStatus.BOOKED:
            raise ConflictException("Only a booked slot can have an outcome")
        config = _config(step.stage)
        if outcome is StepSlotOutcome.RESCHEDULE and not config.get("allow_reschedule", True):
            raise BadRequestException("This interview cannot be rescheduled; record a pass or a fail")

        slot.status = StepSlotStatus.COMPLETED
        slot.outcome = outcome
        slot.outcome_note = (note or "").strip() or None
        if slot.appointment_id:
            appointment = await self.session.get(Appointment, slot.appointment_id)
            if appointment is not None:
                appointment.status = AppointmentStatus.COMPLETED
        self._activity(
            step,
            WorkflowActivityType.OUTCOME,
            user.id,
            f"Outcome: {outcome.value}" + (f" ({slot.outcome_note})" if slot.outcome_note else ""),
        )
        await self.session.commit()

        name = step.stage_name_snapshot
        if outcome is StepSlotOutcome.PASSED:
            await self._complete(application, step, user.id)
            await self._notify_student(application, f"{name} passed", "Well done. The next stage is open.")
        elif outcome is StepSlotOutcome.RESCHEDULE:
            await self._notify_student(
                application,
                f"{name}: another attempt",
                "Your counsellor will offer new slots for another attempt."
                + (f" Their note: {slot.outcome_note}" if slot.outcome_note else ""),
            )
        else:
            await self._fail(application, step, user, slot.outcome_note)
        await self.session.refresh(slot)
        return slot

    async def _fail(
        self, application: Application, step: ApplicationWorkflowStep, user: User, note: str | None
    ) -> None:
        config = _config(step.stage)
        await self.workflows.update_step(step, {"status": WorkflowStepStatus.FAILED}, performed_by=user.id)
        if config.get("fail_ends_journey"):
            workflow = await self._workflow_or_404(application.id)
            for other in workflow.steps:
                if other.status not in _TERMINAL_STEP_STATUSES:
                    other.status = WorkflowStepStatus.CANCELLED
                    other.completed_at = datetime.now(UTC)
            workflow.status = ApplicationWorkflowStatus.CANCELLED
            workflow.completed_at = datetime.now(UTC)
            await self.session.commit()
        fail_status = _status_from_config(config.get("on_fail_status"))
        if fail_status is not None:
            await self.session.refresh(application)
            await ApplicationService(self.session).change_application_status(
                application,
                fail_status,
                performed_by=user.id,
                remarks=f"{step.stage_name_snapshot} not successful" + (f": {note}" if note else ""),
            )
        else:
            await self._notify_student(
                application,
                f"{step.stage_name_snapshot} not successful",
                "Your counsellor will be in touch about what happens next.",
            )

    # -------------------------------------------------------- checklist stages

    async def tick_task(
        self, application: Application, step: ApplicationWorkflowStep, key: str, done: bool, user: User
    ) -> None:
        self._require(step, WorkflowStageKind.CHECKLIST)
        tasks = _config(step.stage).get("tasks") or []
        keys = [t.get("key") for t in tasks if isinstance(t, dict)]
        if key not in keys:
            raise NotFoundException("Task not found")

        ticks = dict((step.progress or {}).get("tasks") or {})
        if done:
            ticks.setdefault(key, datetime.now(UTC).isoformat())
        else:
            ticks.pop(key, None)
        step.progress = {**(step.progress or {}), "tasks": ticks}
        attributes.flag_modified(step, "progress")
        label = next(t.get("label") for t in tasks if t.get("key") == key)
        self._activity(step, WorkflowActivityType.TASK_TICKED, user.id, f"{'Done' if done else 'Not done'}: {label}")
        await self.session.commit()

        if all(k in ticks for k in keys):
            await self._complete(application, step, user.id)

    # ------------------------------------------------------- status → journey

    async def sync_with_status(self, application_id: UUID, new_status: ApplicationStatus, user_id: UUID | None) -> None:
        """Complete the issued step a recorded status stands for.

        Steps before it that never finished are skipped, not completed: an offer
        arriving while the application documents were still open says the
        documents stopped mattering, not that they were handed in.
        """
        workflow = await self._workflow(application_id)
        if workflow is None or workflow.status is not ApplicationWorkflowStatus.ACTIVE:
            return
        target = next(
            (
                s
                for s in workflow.steps
                if _kind(s.stage) is WorkflowStageKind.ISSUED
                and _status_from_config(_config(s.stage).get("milestone_status")) is new_status
            ),
            None,
        )
        if target is None or target.status in _TERMINAL_STEP_STATUSES:
            return

        now = datetime.now(UTC)
        for earlier in workflow.steps:
            if earlier.order < target.order and earlier.status not in _TERMINAL_STEP_STATUSES:
                old = earlier.status
                earlier.status = WorkflowStepStatus.SKIPPED
                earlier.completed_at = now
                self.session.add(
                    WorkflowStepActivity(
                        step_id=earlier.id,
                        activity_type=WorkflowActivityType.STATUS_CHANGED,
                        performed_by=user_id,
                        old_status=old,
                        new_status=WorkflowStepStatus.SKIPPED,
                        comment=f"Skipped: the application moved to {new_status.value}",
                    )
                )
        await self.session.commit()
        await self.workflows.update_step(target, {"status": WorkflowStepStatus.COMPLETED}, performed_by=user_id)

    # ---------------------------------------------------------- switching

    async def switch_template(self, application: Application, template_id: UUID | None, user: User) -> None:
        """Replace an application's workflow with another template's.

        For applications that started on the old standard workflow. What the
        student already uploaded stays: checklist items that hold a document
        are kept, only empty ones from the old template go. Steps up to the
        application's current status are marked completed, so a student with an
        offer lands on interview preparation, not on "upload your passport".
        """
        # Resolved first, before anything is deleted: a template that does not
        # exist, or the one the application is already on, must leave the
        # current workflow exactly as it was.
        if template_id is not None:
            template = await self.session.get(WorkflowTemplate, template_id)
            if template is None or not template.is_active:
                raise NotFoundException("Workflow template not found")
        else:
            try:
                template = await self.workflows.resolve_template(application, None)
            except ValueError as error:
                raise BadRequestException(str(error)) from error

        existing = await self._workflow(application.id)
        if existing is not None:
            if existing.template_id == template.id:
                raise ConflictException("The application is already on this journey")
            await self.session.execute(
                delete(ApplicationChecklistItem).where(
                    ApplicationChecklistItem.application_id == application.id,
                    ApplicationChecklistItem.document_id.is_(None),
                    ApplicationChecklistItem.status == ChecklistItemStatus.PENDING,
                )
            )
            await self.session.delete(existing)
            await self.session.commit()

        try:
            await self.workflows.instantiate(application.id, template.id, performed_by=user.id)
        except ValueError as error:
            raise BadRequestException(str(error)) from error

        await self._fast_forward(application, user.id)

    async def _fast_forward(self, application: Application, user_id: UUID | None) -> None:
        workflow = await self._workflow_or_404(application.id)
        current_rank = _RANK.get(application.status)
        if current_rank is None:
            return
        reached = [
            s.order
            for s in workflow.steps
            if (linked := _linked_status(s.stage)) is not None and _RANK.get(linked, 10**6) <= current_rank
        ]
        if not reached:
            return
        last = max(reached)
        now = datetime.now(UTC)
        for step in workflow.steps:
            if step.order <= last:
                step.status = WorkflowStepStatus.COMPLETED
                step.started_at = step.started_at or now
                step.completed_at = now
                self.session.add(
                    WorkflowStepActivity(
                        step_id=step.id,
                        activity_type=WorkflowActivityType.STATUS_CHANGED,
                        performed_by=user_id,
                        new_status=WorkflowStepStatus.COMPLETED,
                        comment="Already done when the application moved to this journey",
                    )
                )
            elif step.order == last + 1:
                step.status = WorkflowStepStatus.CURRENT
                step.started_at = now
            else:
                step.status = WorkflowStepStatus.PENDING
        if all(s.status is WorkflowStepStatus.COMPLETED for s in workflow.steps):
            workflow.status = ApplicationWorkflowStatus.COMPLETED
            workflow.completed_at = now
        await self.session.commit()
