"""The UK student journey, as workflow-template data.

Ten stages from application to visa, each with a `kind` that decides what the
student and staff do there (see `WorkflowStageKind`). The labels came from the
`student-journey.html` prototype and are only a starting point: once seeded,
the template is edited in the console's template builder like any other.

`ensure_uk_journey_template` is what the test suite and `scripts/` call. The
migration that seeds production carries a frozen copy of this data instead of
importing it, because a migration has to keep meaning what it meant the day it
ran, whatever this module says later.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Country, WorkflowStage, WorkflowStageDocumentRequirement, WorkflowTemplate
from ..models.enums import ApplicationStatus, DocumentType, WorkflowStageKind

UK_JOURNEY_SLUG = "uk-student-journey"

#: (document_type, custom_label, condition). `condition` is `ug`, `pg`, `gap` or None.
_Requirement = tuple[DocumentType, str, str | None]

APPLICATION_DOCUMENTS: list[_Requirement] = [
    (DocumentType.ACADEMIC_CERTIFICATE, "Class 10 academic documents", None),
    (DocumentType.ACADEMIC_CERTIFICATE, "+2 academic documents", None),
    (DocumentType.ACADEMIC_TRANSCRIPT, "Bachelor's transcripts", "pg"),
    (DocumentType.PROVISIONAL_CERTIFICATE, "Bachelor's degree / provisional certificate", "pg"),
    (DocumentType.RECOMMENDATION_LETTER, "Letter of Recommendation (LOR)", None),
    (DocumentType.MEDIUM_OF_INSTRUCTION, "Medium of Instruction (MOI)", None),
    (DocumentType.PASSPORT, "Passport", None),
    (DocumentType.CV, "CV", None),
    (DocumentType.STATEMENT_OF_PURPOSE, "Statement of Purpose (SOP)", None),
    (DocumentType.GAP_EXPLANATION, "Gap explanation & evidence (gap > 6 months)", "gap"),
]

CAS_DOCUMENTS: list[_Requirement] = [
    (DocumentType.FINANCIAL_DOCUMENT, "Financial evidence (bank statement)", None),
    (DocumentType.FINANCIAL_DOCUMENT, "Tuition fee deposit receipt", None),
    (DocumentType.FINANCIAL_DOCUMENT, "Sponsor & relationship documents", None),
]

CAS_SHIELD_DOCUMENTS: list[_Requirement] = [
    (DocumentType.FINANCIAL_DOCUMENT, "Source of funds documents", None),
    (DocumentType.FINANCIAL_DOCUMENT, "Income / tax documents", None),
    (DocumentType.FINANCIAL_DOCUMENT, "Education loan sanction letter", None),
]

INTERVIEW_RESOURCES: list[dict[str, str]] = [
    {"title": "Common interview questions", "description": "Top 40 credibility questions with tips", "url": ""},
    {
        "title": "Course & university research",
        "description": "Template: why this course, why this university",
        "url": "",
    },
    {"title": "Finance & sponsor explanation", "description": "How to talk about your funding clearly", "url": ""},
    {"title": "Practise with a mock interview", "description": "Scored practice in your portal", "url": "/interviews"},
]

VISA_TASKS: list[dict[str, str]] = [
    {"key": "ihs", "label": "Pay Immigration Health Surcharge (IHS)"},
    {"key": "online_form", "label": "Complete online visa application"},
    {"key": "book_biometrics", "label": "Book biometrics appointment"},
    {"key": "upload_documents", "label": "Upload supporting documents"},
    {"key": "attend_biometrics", "label": "Attend biometrics"},
]

#: key, name, kind, config, document requirements, description.
UK_JOURNEY_STAGES: list[dict[str, Any]] = [
    {
        "key": "application",
        "name": "Application",
        "kind": WorkflowStageKind.DOCUMENTS,
        "description": "Upload the documents the university needs for your application.",
        "config": {"on_complete_status": ApplicationStatus.READY_TO_SUBMIT.value, "level_aware": True},
        "documents": APPLICATION_DOCUMENTS,
    },
    {
        "key": "offer",
        "name": "Offer Letter",
        "kind": WorkflowStageKind.ISSUED,
        "description": "The university reviews your application and issues an offer.",
        "config": {"milestone_status": ApplicationStatus.OFFER_RECEIVED.value},
    },
    {
        "key": "interview_prep",
        "name": "Interview Preparation",
        "kind": WorkflowStageKind.REVIEW,
        "description": "Use the resources, prepare your answers and send them to your counsellor.",
        "config": {
            "resources": INTERVIEW_RESOURCES,
            "allow_text": True,
            "allow_document": True,
            "allow_link": False,
            "accept": "document",
        },
    },
    {
        "key": "interview_recording",
        "name": "Interview Recording",
        "kind": WorkflowStageKind.REVIEW,
        "description": "Record yourself answering the practice questions and hand in the video.",
        "config": {"allow_text": False, "allow_document": True, "allow_link": True, "accept": "video"},
    },
    {
        "key": "mock_interview",
        "name": "Mock with University",
        "kind": WorkflowStageKind.BOOKING,
        "description": "Pick a slot for your mock interview with the university.",
        "config": {
            "allow_reschedule": True,
            "fail_ends_journey": False,
            "appointment_type": "university_mock_interview",
        },
    },
    {
        "key": "suitability_interview",
        "name": "Suitability Interview",
        "kind": WorkflowStageKind.BOOKING,
        "description": "Book your final suitability interview with the university.",
        "config": {
            "allow_reschedule": False,
            "fail_ends_journey": True,
            "on_fail_status": ApplicationStatus.REJECTED.value,
            "appointment_type": "suitability_interview",
        },
    },
    {
        "key": "cas_documents",
        "name": "CAS Documents",
        "kind": WorkflowStageKind.DOCUMENTS,
        "description": "Upload the documents the university needs to prepare your CAS.",
        "config": {},
        "documents": CAS_DOCUMENTS,
    },
    {
        "key": "cas_shield",
        "name": "CAS Shield",
        "kind": WorkflowStageKind.DOCUMENTS,
        "description": "Upload the CAS Shield documents for the financial compliance checks.",
        "config": {},
        "documents": CAS_SHIELD_DOCUMENTS,
    },
    {
        "key": "cas_issued",
        "name": "CAS Issued",
        "kind": WorkflowStageKind.ISSUED,
        "description": "The university reviews your CAS documents and issues your CAS.",
        "config": {"milestone_status": ApplicationStatus.CAS_RECEIVED.value},
    },
    {
        "key": "visa",
        "name": "Visa",
        "kind": WorkflowStageKind.CHECKLIST,
        "description": "Complete each step of your student visa application.",
        "config": {"tasks": VISA_TASKS, "on_complete_status": ApplicationStatus.VISA_PROCESSING.value},
    },
]


async def ensure_uk_journey_template(session: AsyncSession) -> WorkflowTemplate:
    """The UK journey template, created if it does not exist yet.

    Bound to the country with ISO code GB when there is one, which is what
    makes `ApplicationWorkflowService.resolve_template` pick it for UK
    applications. Idempotent on the slug: an existing template is returned
    untouched, so edits made in the console are never overwritten.
    """
    existing = await session.scalar(select(WorkflowTemplate).where(WorkflowTemplate.slug == UK_JOURNEY_SLUG))
    if existing is not None:
        return existing

    country_id = await session.scalar(select(Country.id).where(Country.iso2 == "GB"))
    template = WorkflowTemplate(
        name="UK Student Journey",
        slug=UK_JOURNEY_SLUG,
        description="Application to visa for UK universities: offer, interviews, CAS and visa.",
        country_id=country_id,
        is_default=False,
        is_active=True,
    )
    session.add(template)
    await session.flush()

    for order, spec in enumerate(UK_JOURNEY_STAGES):
        stage = WorkflowStage(
            template_id=template.id,
            key=spec["key"],
            name=spec["name"],
            description=spec["description"],
            kind=spec["kind"].value,
            config=spec["config"],
            order=order,
        )
        session.add(stage)
        await session.flush()
        for document_type, label, condition in spec.get("documents", []):
            session.add(
                WorkflowStageDocumentRequirement(
                    stage_id=stage.id,
                    document_type=document_type,
                    custom_label=label,
                    is_required=True,
                    condition=condition,
                )
            )

    await session.commit()
    await session.refresh(template)
    return template
