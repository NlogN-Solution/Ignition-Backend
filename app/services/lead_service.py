from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Lead, LeadActivity, LeadFollowUp, Notification, StudentProfile, User
from ..models.enums import (
    ConversionSource,
    FollowUpOutcome,
    InstitutionType,
    LeadActivityType,
    LeadStatus,
    LostReason,
    NotificationType,
    UserRole,
    UserStatus,
)
from .user_service import UserService

ENGAGED_OUTCOMES = {
    FollowUpOutcome.INTERESTED,
    FollowUpOutcome.VERY_INTERESTED,
    FollowUpOutcome.DOCUMENTS_PENDING,
    FollowUpOutcome.CONVERTED_TO_PROSPECT,
    FollowUpOutcome.CONVERTED_TO_CLIENT,
}

MAX_FOLLOW_UP_ATTEMPTS = 7


class LeadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_lead(self, lead_id: UUID) -> Lead | None:
        query = select(Lead).where(Lead.id == lead_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_activities(self, lead_id: UUID) -> list[LeadActivity]:
        query = select(LeadActivity).where(LeadActivity.lead_id == lead_id).order_by(LeadActivity.created_at.desc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_leads(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        source: str | None = None,
        priority: str | None = None,
        assigned_to: UUID | None = None,
        exclude_status: str | None = None,
    ) -> tuple[list[Lead], int]:
        query = select(Lead)
        count_query = select(func.count()).select_from(Lead)

        if search:
            search_value = f"%{search.strip().lower()}%"
            search_filter = or_(
                func.lower(Lead.first_name).like(search_value),
                func.lower(Lead.last_name).like(search_value),
                func.lower(Lead.email).like(search_value),
                func.lower(Lead.phone).like(search_value),
            )
            query = query.where(search_filter)
            count_query = count_query.where(search_filter)

        if status:
            query = query.where(Lead.status == status)
            count_query = count_query.where(Lead.status == status)

        if statuses:
            query = query.where(Lead.status.in_(statuses))
            count_query = count_query.where(Lead.status.in_(statuses))

        if exclude_status:
            query = query.where(Lead.status != exclude_status)
            count_query = count_query.where(Lead.status != exclude_status)

        if source:
            query = query.where(Lead.source == source)
            count_query = count_query.where(Lead.source == source)

        if priority:
            query = query.where(Lead.priority == priority)
            count_query = count_query.where(Lead.priority == priority)

        if assigned_to:
            query = query.where(Lead.assigned_to == assigned_to)
            count_query = count_query.where(Lead.assigned_to == assigned_to)

        total = await self.session.scalar(count_query)
        query = query.order_by(Lead.created_at.desc()).limit(limit).offset((page - 1) * limit)
        results = await self.session.execute(query)
        return list(results.scalars().all()), total or 0

    async def create_lead(self, data: dict[str, Any], performed_by: UUID | None = None) -> Lead:
        lead = Lead(**data)
        self.session.add(lead)
        await self.session.commit()
        await self.session.refresh(lead)
        await self._log_activity(
            lead,
            LeadActivityType.LEAD_CREATED,
            performed_by=performed_by,
            title="Lead created",
            description="A new lead was created.",
        )
        return lead

    async def update_lead(self, lead: Lead, data: dict[str, Any]) -> Lead:
        old_status = lead.status
        for key, value in data.items():
            if value is not None:
                setattr(lead, key, value)
        await self.session.commit()
        await self.session.refresh(lead)

        if data.get("status") and data["status"] != old_status:
            await self._log_activity(
                lead,
                LeadActivityType.STATUS_CHANGED,
                title="Lead status updated",
                description=f"Status changed from {old_status} to {lead.status}.",
                old_status=old_status,
                new_status=lead.status,
            )

        return lead

    async def delete_lead(self, lead: Lead) -> Lead:
        await self.session.delete(lead)
        await self.session.commit()
        return lead

    async def assign_lead(self, lead: Lead, counsellor_id: UUID, performed_by: UUID | None = None) -> Lead:
        lead.assigned_to = counsellor_id
        await self.session.commit()
        await self.session.refresh(lead)
        await self._log_activity(
            lead,
            LeadActivityType.ASSIGNED,
            performed_by=performed_by,
            title="Lead assigned",
            description=f"Lead assigned to user {counsellor_id}.",
        )
        await self._notify(
            counsellor_id,
            title="New lead assigned",
            message=f"{lead.first_name} {lead.last_name or ''}".strip() + " was assigned to you.",
        )
        return lead

    async def change_lead_status(
        self,
        lead: Lead,
        status: LeadStatus,
        performed_by: UUID | None = None,
        remarks: str | None = None,
    ) -> Lead:
        old_status = lead.status
        lead.status = status
        await self.session.commit()
        await self.session.refresh(lead)
        await self._log_activity(
            lead,
            LeadActivityType.STATUS_CHANGED,
            performed_by=performed_by,
            title="Lead status changed",
            description=remarks or f"Status changed from {old_status} to {status}.",
            old_status=old_status,
            new_status=status,
        )
        return lead

    async def qualify_lead(self, lead: Lead, performed_by: UUID | None = None) -> Lead:
        old_status = lead.status
        lead.status = LeadStatus.QUALIFIED
        lead.qualified_by = performed_by
        lead.qualified_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(lead)
        await self._log_activity(
            lead,
            LeadActivityType.STATUS_CHANGED,
            performed_by=performed_by,
            title="Lead qualified",
            description="Verified as genuinely interested, eligible, and intending to study abroad.",
            old_status=old_status,
            new_status=lead.status,
        )
        await self._notify(
            lead.assigned_to,
            title="Lead qualified",
            message=f"{lead.first_name} {lead.last_name or ''}".strip() + " was qualified as a prospect.",
        )
        return lead

    async def mark_lost(
        self,
        lead: Lead,
        reason: LostReason,
        performed_by: UUID | None = None,
        remarks: str | None = None,
    ) -> Lead:
        old_status = lead.status
        lead.status = LeadStatus.LOST
        lead.lost_reason = reason
        lead.lost_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(lead)
        await self._log_activity(
            lead,
            LeadActivityType.LOST,
            performed_by=performed_by,
            title="Lead marked lost",
            description=remarks or f"Reason: {reason.value}.",
            old_status=old_status,
            new_status=lead.status,
        )
        await self._notify(
            lead.assigned_to,
            title="Lead lost",
            message=f"{lead.first_name} {lead.last_name or ''}".strip() + f" was marked lost ({reason.value}).",
        )
        return lead

    async def convert_lead(
        self,
        lead: Lead,
        converted_user_id: UUID | None = None,
        performed_by: UUID | None = None,
        remarks: str | None = None,
        conversion_source: ConversionSource | None = None,
        create_portal_account: bool = False,
    ) -> tuple[Lead, bool, bool, str | None]:
        old_status = lead.status
        created_new_user = False
        student_user: User | None = None

        if converted_user_id is not None:
            result = await self.session.execute(select(User).where(User.id == converted_user_id))
            student_user = result.scalar_one_or_none()
        else:
            existing_user = None
            if lead.email:
                result = await self.session.execute(select(User).where(User.email == lead.email))
                existing_user = result.scalar_one_or_none()

            if existing_user is not None:
                student_user = existing_user
                converted_user_id = existing_user.id
            else:
                new_user = User(
                    email=lead.email or f"lead-{uuid4().hex}@no-email.ignition",
                    first_name=lead.first_name,
                    last_name=lead.last_name or "",
                    phone=lead.phone,
                    role=UserRole.STUDENT,
                    status=UserStatus.ACTIVE,
                )
                self.session.add(new_user)
                await self.session.flush()

                profile = StudentProfile(
                    user_id=new_user.id,
                    education_level=InstitutionType.BACHELOR,
                    preferred_country=lead.interested_country,
                    preferred_program=lead.interested_course,
                    preferred_intake=lead.preferred_intake,
                    notes=(
                        f"Converted from lead on {datetime.now(UTC):%Y-%m-%d} "
                        f"(source: {lead.source.value if lead.source else 'unknown'})."
                    ),
                )
                self.session.add(profile)
                await self.session.flush()

                student_user = new_user
                converted_user_id = new_user.id
                created_new_user = True

        # Converting only ever creates the student record (needed for every FK —
        # documents, applications, appointments...). Login access is a separate,
        # opt-in step: skip it unless asked, and never overwrite an existing password.
        portal_account_created = False
        generated_password: str | None = None
        if create_portal_account and student_user is not None and not student_user.has_portal_access:
            user_service = UserService(self.session)
            student_user, generated_password = await user_service.reset_password(student_user, None)
            portal_account_created = True

        lead.status = LeadStatus.CONVERTED
        lead.converted_user_id = converted_user_id
        lead.converted_by = performed_by
        lead.converted_at = datetime.now(UTC)
        if conversion_source is not None:
            lead.conversion_source = conversion_source

        await self.session.commit()
        await self.session.refresh(lead)
        await self._log_activity(
            lead,
            LeadActivityType.CONVERTED,
            performed_by=performed_by,
            title="Lead converted",
            description=remarks or f"Lead converted from {old_status} to {lead.status}.",
            old_status=old_status,
            new_status=lead.status,
        )
        await self._log_activity(
            lead,
            LeadActivityType.CONVERTED,
            performed_by=performed_by,
            title="Moved to Student module",
            description="A student record was created and the lead left Lead Management.",
        )
        await self._notify(
            lead.assigned_to,
            title="Lead converted",
            message=f"{lead.first_name} {lead.last_name or ''}".strip() + " converted to a client.",
        )
        return lead, created_new_user, portal_account_created, generated_password

    # --- Follow-ups -----------------------------------------------------

    async def list_follow_ups(self, lead_id: UUID) -> list[LeadFollowUp]:
        result = await self.session.execute(
            select(LeadFollowUp).where(LeadFollowUp.lead_id == lead_id).order_by(LeadFollowUp.attempt_number)
        )
        return list(result.scalars().all())

    async def create_follow_up(
        self, lead: Lead, data: dict[str, Any], performed_by: UUID | None = None
    ) -> LeadFollowUp:
        if lead.status == LeadStatus.LOST:
            raise ValueError("This lead is marked lost — change its status before scheduling another follow-up")

        existing = await self.list_follow_ups(lead.id)
        if len(existing) >= MAX_FOLLOW_UP_ATTEMPTS:
            raise ValueError(f"This lead has already reached the {MAX_FOLLOW_UP_ATTEMPTS} follow-up attempt limit")

        follow_up = LeadFollowUp(
            lead_id=lead.id,
            attempt_number=len(existing) + 1,
            counsellor_id=data.get("counsellor_id") or performed_by,
            scheduled_at=data["scheduled_at"],
            method=data["method"],
            notes=data.get("notes"),
        )
        self.session.add(follow_up)
        await self.session.commit()
        await self.session.refresh(follow_up)

        await self._log_activity(
            lead,
            LeadActivityType.FOLLOW_UP,
            performed_by=performed_by,
            title=f"Follow-up #{follow_up.attempt_number} scheduled",
            description=f"Via {follow_up.method.value}.",
        )
        return follow_up

    async def get_follow_up(self, follow_up_id: UUID) -> LeadFollowUp | None:
        result = await self.session.execute(select(LeadFollowUp).where(LeadFollowUp.id == follow_up_id))
        return result.scalar_one_or_none()

    async def complete_follow_up(
        self,
        lead: Lead,
        follow_up: LeadFollowUp,
        data: dict[str, Any],
        performed_by: UUID | None = None,
    ) -> LeadFollowUp:
        follow_up.completed_at = data.get("completed_at") or datetime.now(UTC)
        if data.get("outcome") is not None:
            follow_up.outcome = data["outcome"]
        if data.get("notes") is not None:
            follow_up.notes = data["notes"]
        if "next_follow_up_at" in data and data["next_follow_up_at"] is not None:
            follow_up.next_follow_up_at = data["next_follow_up_at"]
            lead.next_follow_up_at = data["next_follow_up_at"]

        await self.session.commit()
        await self.session.refresh(follow_up)

        await self._log_activity(
            lead,
            LeadActivityType.FOLLOW_UP,
            performed_by=performed_by,
            title=f"Follow-up #{follow_up.attempt_number} completed",
            description=f"Outcome: {follow_up.outcome.value if follow_up.outcome else 'n/a'}. {follow_up.notes or ''}".strip(),
        )

        await self._check_auto_lost(lead, performed_by=performed_by)
        return follow_up

    async def _check_auto_lost(self, lead: Lead, performed_by: UUID | None = None) -> None:
        if lead.status in (LeadStatus.QUALIFIED, LeadStatus.CONVERTED, LeadStatus.LOST):
            return

        follow_ups = await self.list_follow_ups(lead.id)
        completed = [f for f in follow_ups if f.completed_at is not None]
        if len(completed) < MAX_FOLLOW_UP_ATTEMPTS:
            return
        if any(f.outcome in ENGAGED_OUTCOMES for f in completed):
            return

        old_status = lead.status
        lead.status = LeadStatus.LOST
        lead.lost_reason = LostReason.NO_RESPONSE_AFTER_7_ATTEMPTS
        lead.lost_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(lead)

        await self._log_activity(
            lead,
            LeadActivityType.LOST,
            performed_by=performed_by,
            title="Automatically marked lost",
            description=f"{MAX_FOLLOW_UP_ATTEMPTS} follow-up attempts completed with no engagement.",
            old_status=old_status,
            new_status=lead.status,
        )
        await self._notify(
            lead.assigned_to,
            title="Lead automatically lost",
            message=(
                f"{lead.first_name} {lead.last_name or ''}".strip()
                + f" reached {MAX_FOLLOW_UP_ATTEMPTS} follow-ups with no engagement and was automatically marked lost."
            ),
        )

    async def list_due_follow_ups(
        self,
        page: int,
        limit: int,
        counsellor_id: UUID | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        query = (
            select(LeadFollowUp, Lead.first_name, Lead.last_name, Lead.priority, Lead.assigned_to)
            .join(Lead, Lead.id == LeadFollowUp.lead_id)
            .where(LeadFollowUp.completed_at.is_(None))
        )
        count_query = (
            select(func.count())
            .select_from(LeadFollowUp)
            .join(Lead, Lead.id == LeadFollowUp.lead_id)
            .where(LeadFollowUp.completed_at.is_(None))
        )

        if counsellor_id:
            query = query.where(LeadFollowUp.counsellor_id == counsellor_id)
            count_query = count_query.where(LeadFollowUp.counsellor_id == counsellor_id)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(LeadFollowUp.scheduled_at.asc()).offset((page - 1) * limit).limit(limit)
        result = await self.session.execute(query)

        items = []
        for follow_up, first_name, last_name, priority, assigned_to in result.all():
            items.append(
                {
                    "id": follow_up.id,
                    "lead_id": follow_up.lead_id,
                    "lead_name": f"{first_name} {last_name or ''}".strip(),
                    "lead_priority": priority,
                    "assigned_to": assigned_to,
                    "attempt_number": follow_up.attempt_number,
                    "scheduled_at": follow_up.scheduled_at,
                    "method": follow_up.method,
                    "counsellor_id": follow_up.counsellor_id,
                }
            )
        return items, total

    # --- internal helpers -------------------------------------------------

    async def _log_activity(
        self,
        lead: Lead,
        activity_type: LeadActivityType,
        performed_by: UUID | None = None,
        title: str | None = None,
        description: str | None = None,
        old_status: LeadStatus | None = None,
        new_status: LeadStatus | None = None,
    ) -> None:
        activity = LeadActivity(
            lead_id=lead.id,
            activity_type=activity_type,
            performed_by=performed_by,
            title=title,
            description=description,
            old_status=old_status,
            new_status=new_status,
        )
        self.session.add(activity)
        await self.session.commit()

    async def _notify(self, user_id: UUID | None, title: str, message: str) -> None:
        if user_id is None:
            return
        notification = Notification(
            user_id=user_id,
            type=NotificationType.LEAD,
            title=title,
            message=message,
        )
        self.session.add(notification)
        await self.session.commit()


async def get_lead_service(session: AsyncSession) -> LeadService:
    return LeadService(session)
