"""Recording a milestone: one act, not five that might half-happen.

## The failure this exists to prevent

Before this, moving an application to `offer_received` was a status dropdown
and a remarks box. The date lived behind a different dialog with eight other
dates; the letter was an unrelated upload in the Documents area. So the normal
outcome of a counsellor recording an offer was an application whose status said
an offer existed, with no date and no letter behind it — and the student's
portal showed exactly that: a green badge with nothing to open.

Doing them in sequence from the route is not enough either. Upload succeeds,
status write fails: now there is a letter nobody can find. Status succeeds,
milestone write fails: the student never gets told.

## The ordering, and why it is this one

    1. permissions        (route)
    2. required fields    ← cheapest to reject, so first
    3. store the file     ← the only step that cannot be rolled back
    4. milestone columns  ┐
    5. status + history   │ one transaction
    6. milestone record   ┘
    7. notify             ← after commit; subscribers must see the new state

Validation before upload means a request missing a date never writes bytes to
Cloudinary. The upload sits *outside* the transaction because object storage
cannot join one — so the failure mode is deliberately chosen: if the database
work then fails, an orphaned file exists in Cloudinary that nothing references.
That is a garbage-collection problem. The alternative ordering leaves an
application claiming a letter that was never stored, which is a lie to the
student. An unreferenced blob is the cheaper wrong thing.

Notification comes last and after the commit, because a subscriber reads the
database and must not see the pre-change state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException
from ..core.events import ApplicationStatusChanged, MilestoneRecorded, event_bus
from ..models import Application, ApplicationDocument, ApplicationMilestone, ApplicationStatusHistory, Document, Program
from ..models.enums import ApplicationStatus, OfferType
from .application_service import STRING_DATE_FIELDS
from .status_requirements import WRITABLE_MILESTONE_FIELDS, requirement_for

#: Fields that are dates on the application, and how to coerce them.
#:
#: The six original date columns are `String(10)` — an ED360 wart, documented on
#: the model — while `cas_received_date` is a real `Date`. Passing the wrong
#: Python type to either gets an asyncpg DataError rather than a coercion, so
#: the distinction has to be explicit here.
_STRING_DATE_FIELDS = STRING_DATE_FIELDS
_REAL_DATE_FIELDS = frozenset({"cas_received_date"})
_DECIMAL_FIELDS = frozenset({"tuition_fee", "scholarship_amount"})


def _coerce(field: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if field in _STRING_DATE_FIELDS:
        # Validated as a date, stored as the ISO string the column expects.
        return date.fromisoformat(str(value)[:10]).isoformat()
    if field in _REAL_DATE_FIELDS:
        return date.fromisoformat(str(value)[:10])
    if field in _DECIMAL_FIELDS:
        return Decimal(str(value))
    if field == "offer_type":
        return OfferType(str(value))
    return value


class MilestoneService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        application: Application,
        new_status: ApplicationStatus,
        *,
        fields: dict[str, Any],
        document: Document | None,
        performed_by: uuid.UUID,
        remarks: str | None = None,
    ) -> Application:
        """Apply a milestone status change and everything that comes with it.

        `document` is already stored by the time this is called — the route
        uploads first so a validation failure never writes bytes. Linking it to
        the application happens inside the transaction, so a document that
        cannot be linked does not leave a half-recorded milestone behind.
        """
        requirement = requirement_for(new_status)

        # ── 2. Required fields ───────────────────────────────────────────────
        if requirement is not None:
            if requirement.required_date_field:
                raw = fields.get(requirement.required_date_field)
                if raw in (None, ""):
                    raise BadRequestException(
                        f"{requirement.document_label or 'This status'} needs a date before it can be recorded."
                    )
            if requirement.required_document is not None and document is None:
                # Not fatal if the letter genuinely already exists on file —
                # staff may have uploaded it minutes earlier from the documents
                # screen. Checked rather than assumed.
                existing = await self.session.scalar(
                    select(Document.id)
                    .join(ApplicationDocument, ApplicationDocument.document_id == Document.id)
                    .where(
                        ApplicationDocument.application_id == application.id,
                        Document.document_type == requirement.required_document,
                    )
                )
                if existing is None:
                    raise BadRequestException(
                        f"Upload the {requirement.document_label.lower()} before recording this status."
                    )

        allowed = set(WRITABLE_MILESTONE_FIELDS)
        unknown = set(fields) - allowed
        if unknown:
            # The allowlist is the backstop that stops the milestone path being
            # used to write `status` or `student_id` and bypass the audit trail.
            raise BadRequestException(f"Cannot set: {', '.join(sorted(unknown))}")

        old_status = application.status

        # ── 4/5/6. One transaction ───────────────────────────────────────────
        try:
            for field, value in fields.items():
                try:
                    setattr(application, field, _coerce(field, value))
                except (ValueError, InvalidOperation) as error:
                    raise BadRequestException(f"{field} is not a valid value: {value}") from error

            if document is not None:
                already_linked = await self.session.scalar(
                    select(ApplicationDocument.id).where(
                        ApplicationDocument.application_id == application.id,
                        ApplicationDocument.document_id == document.id,
                    )
                )
                if already_linked is None:
                    self.session.add(
                        ApplicationDocument(application_id=application.id, document_id=document.id)
                    )

            if new_status != old_status:
                application.status = new_status
                self.session.add(
                    ApplicationStatusHistory(
                        application_id=application.id,
                        old_status=old_status,
                        new_status=new_status,
                        changed_by=performed_by,
                        remarks=remarks,
                    )
                )

            milestone_created = False
            if requirement is not None and requirement.milestone is not None:
                existing_milestone = await self.session.scalar(
                    select(ApplicationMilestone).where(
                        ApplicationMilestone.application_id == application.id,
                        ApplicationMilestone.kind == requirement.milestone,
                    )
                )
                occurred = self._occurred_at(fields, requirement.required_date_field)
                if existing_milestone is None:
                    self.session.add(
                        ApplicationMilestone(
                            student_id=application.student_id,
                            application_id=application.id,
                            kind=requirement.milestone,
                            occurred_at=occurred,
                        )
                    )
                    milestone_created = True
                else:
                    # Correcting a date must not re-fire the celebration. The
                    # milestone's `seen_at` is left exactly as it was.
                    existing_milestone.occurred_at = occurred

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        await self.session.refresh(application)

        # ── 7. Events, after the commit ──────────────────────────────────────
        if new_status != old_status:
            await event_bus.publish(
                ApplicationStatusChanged(
                    application_id=application.id,
                    student_id=application.student_id,
                    old_status=old_status.value if old_status else None,
                    new_status=new_status.value,
                    changed_by=performed_by,
                    remarks=remarks,
                ),
                self.session,
            )

        if milestone_created and requirement is not None and requirement.milestone is not None:
            program = await self.session.scalar(
                select(Program).options(selectinload(Program.university)).where(Program.id == application.program_id)
            )
            await event_bus.publish(
                MilestoneRecorded(
                    application_id=application.id,
                    student_id=application.student_id,
                    kind=requirement.milestone.value,
                    program_name=program.name if program else "your course",
                    university_name=(
                        program.university.name if program and program.university else None
                    ),
                    occurred_on=str(fields.get(requirement.required_date_field or "") or "") or None,
                ),
                self.session,
            )

        return application

    @staticmethod
    def _occurred_at(fields: dict[str, Any], date_field: str | None) -> datetime:
        """When the milestone happened, from the date staff recorded.

        Falls back to now only when there is no date field at all — never to
        paper over a missing one, because that is rejected above.
        """
        raw = fields.get(date_field or "")
        if not raw:
            return datetime.now(UTC)
        try:
            return datetime.combine(date.fromisoformat(str(raw)[:10]), datetime.min.time(), tzinfo=UTC)
        except ValueError:
            return datetime.now(UTC)

    # --- the student's side --------------------------------------------------

    async def unseen_for(self, student_id: uuid.UUID) -> list[ApplicationMilestone]:
        """Milestones this student has not been shown yet.

        What the dashboard's celebration reads. Ordered oldest first so a
        student who has been away sees the offer before the CAS that followed
        it, which is the order the news actually arrived in.
        """
        result = await self.session.execute(
            select(ApplicationMilestone)
            .options(
                selectinload(ApplicationMilestone.application)
                .selectinload(Application.program)
                .selectinload(Program.university)
            )
            .where(
                ApplicationMilestone.student_id == student_id,
                ApplicationMilestone.seen_at.is_(None),
            )
            .order_by(ApplicationMilestone.occurred_at)
        )
        return list(result.scalars().unique().all())

    async def mark_seen(self, student_id: uuid.UUID, milestone_id: uuid.UUID) -> bool:
        """Acknowledge one milestone. Scoped to the student, so a guessed id
        cannot dismiss somebody else's news."""
        milestone = await self.session.scalar(
            select(ApplicationMilestone).where(
                ApplicationMilestone.id == milestone_id,
                ApplicationMilestone.student_id == student_id,
            )
        )
        if milestone is None:
            return False
        if milestone.seen_at is None:
            milestone.seen_at = datetime.now(UTC)
            await self.session.commit()
        return True

    async def for_application(self, application_id: uuid.UUID) -> list[ApplicationMilestone]:
        result = await self.session.execute(
            select(ApplicationMilestone)
            .where(ApplicationMilestone.application_id == application_id)
            .order_by(ApplicationMilestone.occurred_at)
        )
        return list(result.scalars().all())


async def get_milestone_service(session: AsyncSession = Depends(get_db_session)) -> MilestoneService:
    return MilestoneService(session)
