"""Storing and serving eligibility assessments.

The shape of this module follows one decision: **an assessment is a submission,
the lead is the workflow.** So there is nothing here that changes a status,
assigns a counsellor, writes a note or schedules a follow-up — all four already
exist on `LeadService` and are reached through the lead this assessment belongs
to. What lives here is creating the pair, and reading them back joined.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import EligibilityAssessment, Lead, University, User
from ..models.enums import (
    EligibilityOverall,
    LeadActivityType,
    LeadPriority,
    LeadSource,
    LeadStatus,
    NotificationType,
    UserRole,
)
from ..models.lead import LeadActivity, LeadFollowUp
from .eligibility_rules import assess
from .notification_service import NotificationService

#: Staff who are told when an assessment arrives, and who may read them.
ELIGIBILITY_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.COUNSELLOR)

#: How long an *identical* resubmission is treated as the same one.
#:
#: The guard is on content as well as time, and both halves matter. Time alone
#: would swallow a student who fixed a typo and submitted again a minute later
#: — a real second submission, and the one most likely to be the accurate one.
#: Content alone would collapse a genuine retake weeks later into the first
#: assessment, losing the history. Together they catch what actually happens:
#: a double-clicked button, a browser retry, an impatient refresh.
_DUPLICATE_WINDOW_SECONDS = 120


def _split_name(full_name: str) -> tuple[str, str | None]:
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


class EligibilityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Submitting -----------------------------------------------------------

    async def _find_lead(self, email: str | None, phone: str) -> Lead | None:
        """An existing lead for the same person.

        Email first because `leads.email` is unique where not null, so a second
        lead with the same address is not merely untidy — it will not insert.
        Phone is the fallback for the same person coming back with a different
        address.
        """
        clauses = [Lead.phone == phone]
        if email:
            clauses.append(Lead.email == email)
        return await self.session.scalar(select(Lead).where(or_(*clauses)).order_by(Lead.created_at.desc()).limit(1))

    async def _recent_duplicate(self, lead: Lead, payload: dict[str, Any]) -> EligibilityAssessment | None:
        cutoff = datetime.now(UTC).timestamp() - _DUPLICATE_WINDOW_SECONDS
        latest = await self.session.scalar(
            select(EligibilityAssessment)
            .where(EligibilityAssessment.lead_id == lead.id)
            .order_by(EligibilityAssessment.submitted_at.desc())
            .limit(1)
        )
        if latest is None or latest.submitted_at.timestamp() < cutoff:
            return None

        same_answers = all(
            (getattr(latest, section) or {}) == (payload.get(section) or {})
            for section in ("education", "english", "course", "finance", "documents")
        )
        return latest if same_answers else None

    async def submit(self, payload: dict[str, Any], *, user: User | None = None) -> EligibilityAssessment:
        """Turn a completed form into a lead plus an assessment.

        Returns the existing record when the same lead submitted moments ago,
        so a double-clicked button produces one assessment and one notification
        rather than two of each.
        """
        contact = payload["contact"]
        course = payload["course"]
        first_name, last_name = _split_name(contact["full_name"])

        lead = await self._find_lead(contact.get("email"), contact["phone"])
        if lead is None:
            lead = Lead(
                first_name=first_name,
                last_name=last_name,
                email=contact.get("email"),
                phone=contact["phone"],
                source=LeadSource.WEBSITE,
                status=LeadStatus.NEW,
                # An assessment is several minutes of deliberate work with a
                # phone number attached, which is a warmer signal than a
                # newsletter box. It is not HOT — nobody has spoken to them.
                priority=LeadPriority.WARM,
                interested_country="United Kingdom",
                interested_course=course.get("preferred_course"),
                tags=["eligibility-assessment"],
            )
            self.session.add(lead)
            await self.session.commit()
            await self.session.refresh(lead)
            self.session.add(
                LeadActivity(
                    lead_id=lead.id,
                    activity_type=LeadActivityType.LEAD_CREATED,
                    title="Lead created from eligibility assessment",
                    description="The student completed the public eligibility assessment.",
                )
            )
            await self.session.commit()
        else:
            existing = await self._recent_duplicate(lead, payload)
            if existing is not None:
                return existing
            # A returning student is new information, not a new person. Only
            # fill blanks; never overwrite what staff may have corrected.
            if not lead.interested_course and course.get("preferred_course"):
                lead.interested_course = course["preferred_course"]
            if lead.email is None and contact.get("email"):
                lead.email = contact["email"]
            await self.session.commit()

        verdict = assess(payload)
        assessment = EligibilityAssessment(
            lead_id=lead.id,
            user_id=user.id if user else None,
            education=payload["education"],
            english=payload["english"],
            course=course,
            finance=payload["finance"],
            documents=payload["documents"],
            study_level=course.get("study_level"),
            preferred_course=course.get("preferred_course"),
            preferred_location=course.get("preferred_location"),
            preferred_universities=course.get("preferred_universities") or [],
            source_page=payload.get("source_page"),
            message=contact.get("message"),
            consent_at=datetime.now(UTC) if contact.get("consent") else None,
            submitted_at=datetime.now(UTC),
            **verdict,
        )
        self.session.add(assessment)
        await self.session.commit()
        await self.session.refresh(assessment)

        await self._log_submission(lead, assessment)
        await self._notify_staff(lead, assessment)
        return assessment

    async def _log_submission(self, lead: Lead, assessment: EligibilityAssessment) -> None:
        """Put the submission on the lead's own timeline.

        A counsellor opening the lead should see that an assessment arrived
        without having to know this feature exists.
        """
        self.session.add(
            LeadActivity(
                lead_id=lead.id,
                activity_type=LeadActivityType.NOTE,
                title="Eligibility assessment submitted",
                description=(
                    f"Preliminary assessment: {assessment.overall_status.value.replace('_', ' ')}. "
                    f"Document readiness {assessment.document_readiness}%."
                ),
            )
        )
        await self.session.commit()

    async def _notify_staff(self, lead: Lead, assessment: EligibilityAssessment) -> None:
        """Tell the people who can act on it.

        Assigned counsellor if there is one, otherwise everyone who works the
        queue — an unassigned assessment that notifies nobody is the failure
        mode worth avoiding.
        """
        if lead.assigned_to is not None:
            recipients = [lead.assigned_to]
        else:
            recipients = list(
                (
                    await self.session.execute(
                        select(User.id).where(User.role.in_(ELIGIBILITY_ROLES), User.deleted_at.is_(None))
                    )
                ).scalars()
            )

        name = " ".join(filter(None, [lead.first_name, lead.last_name]))
        await NotificationService(self.session).notify_many(
            recipients,
            notification_type=NotificationType.LEAD,
            title="New eligibility assessment received",
            message=(
                f"{name} — {assessment.preferred_course or 'course not specified'} — "
                f"{assessment.overall_status.value.replace('_', ' ')}"
            ),
        )

    # --- Reading --------------------------------------------------------------

    def _base_query(self):
        return select(EligibilityAssessment).options(
            selectinload(EligibilityAssessment.lead).selectinload(Lead.assignee)
        )

    async def list_assessments(
        self,
        page: int,
        limit: int,
        *,
        search: str | None = None,
        overall_status: str | None = None,
        lead_status: str | None = None,
        assigned_to: UUID | None = None,
        study_level: str | None = None,
        unassigned: bool | None = None,
        sort: str = "newest",
    ) -> tuple[list[EligibilityAssessment], int]:
        query = self._base_query().join(Lead, EligibilityAssessment.lead_id == Lead.id)
        count_query = select(func.count(EligibilityAssessment.id)).join(
            Lead, EligibilityAssessment.lead_id == Lead.id
        )

        filters = []
        if search:
            needle = f"%{search.lower()}%"
            filters.append(
                or_(
                    func.lower(Lead.first_name).like(needle),
                    func.lower(func.coalesce(Lead.last_name, "")).like(needle),
                    func.lower(func.coalesce(Lead.email, "")).like(needle),
                    Lead.phone.like(needle),
                    func.lower(func.coalesce(EligibilityAssessment.preferred_course, "")).like(needle),
                )
            )
        if overall_status:
            filters.append(EligibilityAssessment.overall_status == overall_status)
        if lead_status:
            filters.append(Lead.status == lead_status)
        if assigned_to:
            filters.append(Lead.assigned_to == assigned_to)
        if study_level:
            filters.append(EligibilityAssessment.study_level == study_level)
        if unassigned:
            filters.append(Lead.assigned_to.is_(None))

        for clause in filters:
            query = query.where(clause)
            count_query = count_query.where(clause)

        orders = {
            "newest": EligibilityAssessment.submitted_at.desc(),
            "oldest": EligibilityAssessment.submitted_at.asc(),
            "status": Lead.status.asc(),
            "follow_up": Lead.next_follow_up_at.asc().nullslast(),
        }
        query = query.order_by(orders.get(sort, orders["newest"]))

        total = await self.session.scalar(count_query) or 0
        rows = (
            (await self.session.execute(query.offset((page - 1) * limit).limit(limit))).scalars().unique().all()
        )
        return list(rows), total

    async def get_assessment(self, assessment_id: UUID) -> EligibilityAssessment | None:
        return await self.session.scalar(
            self._base_query().where(EligibilityAssessment.id == assessment_id)
        )

    async def latest_for_user(self, user_id: UUID) -> EligibilityAssessment | None:
        return await self.session.scalar(
            self._base_query()
            .where(EligibilityAssessment.user_id == user_id)
            .order_by(EligibilityAssessment.submitted_at.desc())
            .limit(1)
        )

    async def resolve_universities(self, slugs: list[str] | None) -> list[dict[str, Any]]:
        """Preferred universities, as catalogue rows.

        Same rule as the research handoff: these are slugs from the shared
        catalogue, so they resolve — and one that no longer does is simply
        left out rather than shown as a broken row.
        """
        if not slugs:
            return []
        rows = (
            (await self.session.execute(select(University).where(University.slug.in_(slugs)))).scalars().all()
        )
        by_slug = {row.slug: row for row in rows}
        return [
            {"slug": slug, "name": by_slug[slug].name, "city": by_slug[slug].city}
            for slug in slugs
            if slug in by_slug
        ]

    async def last_contacted(self, lead_id: UUID) -> datetime | None:
        """When someone last actually reached them.

        `completed_at` on a follow-up, not `scheduled_at`: a follow-up that was
        booked and never made is not contact, and showing it as such is how a
        student ends up untouched for a fortnight while the list says otherwise.
        """
        return await self.session.scalar(
            select(func.max(LeadFollowUp.completed_at)).where(LeadFollowUp.lead_id == lead_id)
        )

    async def stats(self) -> dict[str, int]:
        """The summary strip. One grouped query per axis, not one per tile."""
        overall_rows = (
            await self.session.execute(
                select(EligibilityAssessment.overall_status, func.count(EligibilityAssessment.id)).group_by(
                    EligibilityAssessment.overall_status
                )
            )
        ).all()
        by_overall: dict[EligibilityOverall, int] = dict(overall_rows)  # type: ignore[arg-type]
        lead_rows = (
            await self.session.execute(
                select(Lead.status, func.count(EligibilityAssessment.id))
                .join(EligibilityAssessment, EligibilityAssessment.lead_id == Lead.id)
                .group_by(Lead.status)
            )
        ).all()
        by_lead_status: dict[LeadStatus, int] = dict(lead_rows)  # type: ignore[arg-type]
        unassigned = await self.session.scalar(
            select(func.count(EligibilityAssessment.id))
            .join(Lead, EligibilityAssessment.lead_id == Lead.id)
            .where(Lead.assigned_to.is_(None))
        )

        return {
            "total": sum(by_overall.values()),
            "likely_eligible": by_overall.get(EligibilityOverall.PRELIMINARY_LIKELY_ELIGIBLE, 0),
            "needs_review": by_overall.get(EligibilityOverall.NEEDS_COUNSELLOR_REVIEW, 0),
            "more_information_required": by_overall.get(EligibilityOverall.MORE_INFORMATION_REQUIRED, 0),
            "new": by_lead_status.get(LeadStatus.NEW, 0),
            "contacted": by_lead_status.get(LeadStatus.CONTACTED, 0),
            "converted": by_lead_status.get(LeadStatus.CONVERTED, 0),
            "unassigned": unassigned or 0,
        }


async def get_eligibility_service(session: AsyncSession = Depends(get_db_session)) -> EligibilityService:
    return EligibilityService(session)
