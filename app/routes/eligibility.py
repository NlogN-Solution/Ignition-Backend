"""Staff access to eligibility assessments.

Read-only by design, and short for the same reason. Everything a counsellor
*does* to one of these — change the status, take it on, write a note, book a
follow-up, convert it — happens on the lead it belongs to, through
`app/routes/leads.py`, which already implements all five and logs each to the
lead's timeline. Duplicating any of that here would give a submission two
statuses and two note threads with no rule about which is authoritative.

So this module answers two questions and no others: *what has come in*, and
*what did this person tell us*.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role
from ..api.exceptions import NotFoundException
from ..models import EligibilityAssessment
from ..models.enums import UserRole
from ..schemas.eligibility import (
    EligibilityAssessmentDetail,
    EligibilityAssessmentList,
    EligibilityAssessmentRead,
    EligibilityContact,
    EligibilityStats,
    EligibilityUniversity,
)
from ..services.eligibility_service import EligibilityService, get_eligibility_service

router = APIRouter(prefix="/eligibility-assessments", tags=["Eligibility"])

#: Marketing is deliberately not here. They may work the top of the funnel
#: (`leads`), but an assessment carries a named person's grades, finances and
#: passport readiness, and that is a counselling record.
_STAFF = require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.COUNSELLOR)


def _english_summary(assessment: EligibilityAssessment) -> str | None:
    """"IELTS 6.5", for the list column. One line, or nothing."""
    english = assessment.english or {}
    evidence = english.get("evidence")
    if not isinstance(evidence, str):
        return None
    if evidence == "not_taken":
        return "Not taken"
    if evidence == "moi":
        return "MOI"
    score = english.get("overall_score")
    label = evidence.upper() if evidence != "other_test" else (english.get("other_test_name") or "Other")
    return f"{label} {score:g}" if isinstance(score, int | float) else str(label)


async def _to_row(
    assessment: EligibilityAssessment, service: EligibilityService
) -> EligibilityAssessmentRead:
    lead = assessment.lead
    assignee = lead.assignee
    return EligibilityAssessmentRead(
        id=assessment.id,
        lead_id=lead.id,
        contact=EligibilityContact(
            full_name=" ".join(filter(None, [lead.first_name, lead.last_name])),
            email=lead.email,
            phone=lead.phone,
            country=(assessment.education or {}).get("country"),
            preferred_contact_method=None,
        ),
        study_level=assessment.study_level,
        preferred_course=assessment.preferred_course,
        english_summary=_english_summary(assessment),
        academic_status=assessment.academic_status,
        english_status=assessment.english_status,
        financial_status=assessment.financial_status,
        document_status=assessment.document_status,
        overall_status=assessment.overall_status,
        document_readiness=assessment.document_readiness,
        lead_status=lead.status,
        assigned_to=lead.assigned_to,
        assigned_to_name=(
            " ".join(filter(None, [assignee.first_name, assignee.last_name])) if assignee else None
        ),
        last_contacted_at=await service.last_contacted(lead.id),
        next_follow_up_at=lead.next_follow_up_at,
        submitted_at=assessment.submitted_at,
    )


@router.get("", response_model=EligibilityAssessmentList, summary="List eligibility assessments")
async def list_assessments(
    page: int = 1,
    limit: int = 25,
    search: str | None = None,
    overall_status: str | None = None,
    lead_status: str | None = None,
    assigned_to: UUID | None = None,
    study_level: str | None = None,
    unassigned: bool | None = None,
    sort: str = "newest",
    service: EligibilityService = Depends(get_eligibility_service),
    _: object = Depends(_STAFF),
) -> EligibilityAssessmentList:
    items, total = await service.list_assessments(
        page,
        min(limit, 100),
        search=search,
        overall_status=overall_status,
        lead_status=lead_status,
        assigned_to=assigned_to,
        study_level=study_level,
        unassigned=unassigned,
        sort=sort,
    )
    rows = [await _to_row(item, service) for item in items]
    return EligibilityAssessmentList(items=rows, total=total, page=page, limit=limit)


@router.get("/stats", response_model=EligibilityStats, summary="Eligibility queue summary")
async def eligibility_stats(
    service: EligibilityService = Depends(get_eligibility_service),
    _: object = Depends(_STAFF),
) -> EligibilityStats:
    return EligibilityStats(**await service.stats())


@router.get(
    "/{assessment_id}",
    response_model=EligibilityAssessmentDetail,
    summary="One eligibility assessment in full",
)
async def get_assessment(
    assessment_id: UUID,
    service: EligibilityService = Depends(get_eligibility_service),
    _: object = Depends(_STAFF),
) -> EligibilityAssessmentDetail:
    assessment = await service.get_assessment(assessment_id)
    if assessment is None:
        raise NotFoundException("Eligibility assessment not found")

    row = await _to_row(assessment, service)
    contact = row.contact.model_copy(
        update={
            "country": (assessment.course or {}).get("country")
            or (assessment.education or {}).get("country"),
            "preferred_contact_method": (assessment.course or {}).get("preferred_contact_method"),
        }
    )

    return EligibilityAssessmentDetail(
        **row.model_dump(exclude={"contact"}),
        contact=contact,
        user_id=assessment.user_id,
        education=assessment.education or {},
        english=assessment.english or {},
        course=assessment.course or {},
        finance=assessment.finance or {},
        documents=assessment.documents or {},
        preferred_location=assessment.preferred_location,
        preferred_universities=[
            EligibilityUniversity(**entry)
            for entry in await service.resolve_universities(assessment.preferred_universities)
        ],
        assessment_notes=assessment.assessment_notes or [],
        assessment_version=assessment.assessment_version,
        source_page=assessment.source_page,
        message=assessment.message,
        consent_at=assessment.consent_at,
    )
