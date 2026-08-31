"""Schemas for the public eligibility assessment.

Two audiences with very different rights, so two shapes:

* `EligibilitySubmission` / `EligibilityResultPublic` — what an anonymous
  visitor may send and what they get back. The result is deliberately thin:
  a status, a readiness figure and reassurance. It carries no internal notes,
  no lead id, no counsellor, and no explanation of how the verdict was reached.
* `EligibilityAssessmentRead` / `...Detail` — what staff see, which is
  everything.

Never widen the public one by inheriting from the staff one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ..models.enums import (
    ContactMethod,
    DocumentReadiness,
    EligibilityIndicator,
    EligibilityOverall,
    EnglishEvidence,
    FundingSource,
    LeadStatus,
)

# --- What a student sends ------------------------------------------------------

QUALIFICATIONS = ("plus_two", "a_levels", "bachelors", "masters", "diploma", "other")
STUDY_LEVELS = ("foundation", "undergraduate", "postgraduate", "other")


class EducationAnswers(BaseModel):
    highest_qualification: Literal[QUALIFICATIONS]  # type: ignore[valid-type]
    subject: str | None = Field(default=None, max_length=160)
    #: Free text on purpose — "First class", "3.4", "78%", "distinction" are
    #: all real answers from students educated under different systems, and
    #: forcing them into a number here would only invite a wrong one.
    grade: str | None = Field(default=None, max_length=60)
    institution: str | None = Field(default=None, max_length=200)
    completion_year: int | None = Field(default=None, ge=1950, le=2100)


class EnglishAnswers(BaseModel):
    evidence: EnglishEvidence
    overall_score: float | None = Field(default=None, ge=0, le=120)
    listening: float | None = Field(default=None, ge=0, le=120)
    reading: float | None = Field(default=None, ge=0, le=120)
    writing: float | None = Field(default=None, ge=0, le=120)
    speaking: float | None = Field(default=None, ge=0, le=120)
    other_test_name: str | None = Field(default=None, max_length=80)


class CourseAnswers(BaseModel):
    study_level: Literal[STUDY_LEVELS]  # type: ignore[valid-type]
    preferred_course: str | None = Field(default=None, max_length=200)
    preferred_location: str | None = Field(default=None, max_length=100)
    #: Catalogue slugs. Validated as strings only — an unknown slug is dropped
    #: on read rather than rejected here, because the catalogue can change
    #: between a student opening the form and submitting it.
    preferred_universities: list[str] = Field(default_factory=list, max_length=12)


class FinanceAnswers(BaseModel):
    funding_source: FundingSource
    estimated_funds: float | None = Field(default=None, ge=0, le=1_000_000_000)
    sponsor_relationship: str | None = Field(default=None, max_length=80)
    loan_amount: float | None = Field(default=None, ge=0, le=1_000_000_000)
    scholarship_amount: float | None = Field(default=None, ge=0, le=1_000_000_000)
    notes: str | None = Field(default=None, max_length=1000)


class DocumentAnswers(BaseModel):
    academic: DocumentReadiness | None = None
    passport: DocumentReadiness | None = None
    english: DocumentReadiness | None = None
    financial: DocumentReadiness | None = None
    personal: DocumentReadiness | None = None


class ContactAnswers(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    phone: str = Field(min_length=5, max_length=20)
    country: str | None = Field(default=None, max_length=100)
    preferred_contact_method: ContactMethod = ContactMethod.EMAIL
    message: str | None = Field(default=None, max_length=2000)
    #: Must be true. The form cannot be submitted without it, and a lead with
    #: no recorded consent is one nobody may lawfully ring.
    consent: bool

    @field_validator("consent")
    @classmethod
    def _must_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Consent to be contacted is required to submit an assessment.")
        return value

    @field_validator("phone")
    @classmethod
    def _plausible_phone(cls, value: str) -> str:
        digits = [character for character in value if character.isdigit()]
        if len(digits) < 6:
            raise ValueError("Please enter a phone number we can reach you on.")
        return value.strip()


class EligibilitySubmission(BaseModel):
    """One completed assessment, as the public form posts it."""

    education: EducationAnswers
    english: EnglishAnswers
    course: CourseAnswers
    finance: FinanceAnswers
    documents: DocumentAnswers
    contact: ContactAnswers
    source_page: str | None = Field(default=None, max_length=255)


# --- What a student gets back --------------------------------------------------


class EligibilityResultPublic(BaseModel):
    """The confirmation screen, and nothing more.

    No lead id, no counsellor, no rule explanations: an anonymous caller who
    can post a form must not be able to read back how the funnel scores people,
    and the student's own next step does not depend on knowing.
    """

    reference: str
    overall_status: EligibilityOverall
    document_readiness: int
    #: One paragraph, already worded for a student rather than for staff.
    summary: str


# --- What staff see ------------------------------------------------------------


class EligibilityContact(BaseModel):
    full_name: str
    email: str | None = None
    phone: str
    country: str | None = None
    preferred_contact_method: ContactMethod | None = None


class EligibilityUniversity(BaseModel):
    """A preferred university, resolved against the shared catalogue."""

    slug: str
    name: str
    city: str | None = None


class EligibilityAssessmentRead(BaseModel):
    """A row in the staff list."""

    id: UUID
    lead_id: UUID
    contact: EligibilityContact
    study_level: str | None = None
    preferred_course: str | None = None
    english_summary: str | None = None
    academic_status: EligibilityIndicator
    english_status: EligibilityIndicator
    financial_status: EligibilityIndicator
    document_status: EligibilityIndicator
    overall_status: EligibilityOverall
    document_readiness: int
    #: From the lead, because the lead is where the workflow lives.
    lead_status: LeadStatus
    assigned_to: UUID | None = None
    assigned_to_name: str | None = None
    last_contacted_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    submitted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EligibilityAssessmentList(BaseModel):
    items: list[EligibilityAssessmentRead]
    total: int
    page: int
    limit: int


class EligibilityAssessmentDetail(EligibilityAssessmentRead):
    """Everything, for the one submission a counsellor has opened."""

    user_id: UUID | None = None
    education: dict[str, Any]
    english: dict[str, Any]
    course: dict[str, Any]
    finance: dict[str, Any]
    documents: dict[str, Any]
    preferred_location: str | None = None
    preferred_universities: list[EligibilityUniversity] = []
    assessment_notes: list[str] = []
    assessment_version: str
    source_page: str | None = None
    message: str | None = None
    consent_at: datetime | None = None


class EligibilityStats(BaseModel):
    """The counts behind the small summary strip on the list page."""

    total: int
    new: int
    likely_eligible: int
    needs_review: int
    more_information_required: int
    contacted: int
    converted: int
    unassigned: int
