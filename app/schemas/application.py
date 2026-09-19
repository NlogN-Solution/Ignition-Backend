from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import ApplicationStatus, DegreeLevel, OfferType


class ApplicationBase(BaseModel):
    student_id: UUID
    program_id: UUID
    counsellor_id: UUID | None = None
    status: ApplicationStatus | None = ApplicationStatus.DRAFT
    application_date: date | None = None
    submission_date: date | None = None
    offer_received_date: date | None = None
    visa_applied_date: date | None = None
    visa_decision_date: date | None = None
    enrollment_date: date | None = None
    #: The CAS trio. Declared here rather than only on `ApplicationRead`
    #: because staff create and correct them through the same shapes as
    #: everything else — and a column the API stores but never returns is
    #: indistinguishable from a column that was never written.
    cas_received_date: date | None = None
    cas_number: str | None = None
    offer_type: OfferType | None = None
    tuition_fee: float | None = None
    scholarship_amount: float | None = None
    university_application_id: str | None = None
    study_mode: str | None = Field(default=None, max_length=50)
    intake_id: UUID | None = None
    remarks: str | None = None
    #: Staff-set, shown to the student under "Key Deadlines" / "Please note".
    application_deadline: date | None = None
    payment_deadline: date | None = None
    condition_deadline: date | None = None
    student_notice: str | None = None


class ApplicationCreate(ApplicationBase):
    pass


class StudentApplicationCreate(BaseModel):
    """What a student may say when they open their own application.

    Three fields, and the omissions are the point. `student_id` is the caller,
    `status` is always DRAFT, and every date and money field is staff's to set
    — a student posting `{"status": "enrolled"}` or a tuition figure of their
    choosing is exactly the hole the staff `POST /applications` endpoint was
    locked down to avoid (see its docstring). This is that endpoint's
    student-facing counterpart, and it is a different, much smaller shape
    rather than the same one behind a different guard.
    """

    program_id: UUID
    intake_id: UUID | None = None
    #: The student's own note to their counsellor. Free text, not a decision.
    remarks: str | None = Field(default=None, max_length=2000)


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus
    remarks: str | None = None


class ApplicationUpdate(BaseModel):
    """Editable fields.

    `status` is absent on purpose. ED360 includes it and applies it straight to
    the row, so `PATCH {"status": "enrolled"}` moves an application without
    writing an `application_status_history` entry — the audit trail the
    dedicated `POST /{id}/status` endpoint exists to maintain is bypassable
    simply by using the other verb. Here status changes have one door.
    """

    student_id: UUID | None = None
    program_id: UUID | None = None
    counsellor_id: UUID | None = None
    application_date: date | None = None
    submission_date: date | None = None
    offer_received_date: date | None = None
    visa_applied_date: date | None = None
    visa_decision_date: date | None = None
    enrollment_date: date | None = None
    #: The CAS trio. Declared here rather than only on `ApplicationRead`
    #: because staff create and correct them through the same shapes as
    #: everything else — and a column the API stores but never returns is
    #: indistinguishable from a column that was never written.
    cas_received_date: date | None = None
    cas_number: str | None = None
    offer_type: OfferType | None = None
    tuition_fee: float | None = None
    scholarship_amount: float | None = None
    university_application_id: str | None = None
    study_mode: str | None = Field(default=None, max_length=50)
    intake_id: UUID | None = None
    remarks: str | None = None
    #: Staff-set, shown to the student under "Key Deadlines" / "Please note".
    application_deadline: date | None = None
    payment_deadline: date | None = None
    condition_deadline: date | None = None
    student_notice: str | None = None


class ApplicationStatusHistoryRead(BaseModel):
    id: UUID
    old_status: ApplicationStatus | None = None
    new_status: ApplicationStatus
    changed_by: UUID | None = None
    remarks: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ApplicationRead(ApplicationBase):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ApplicationList(BaseModel):
    items: list[ApplicationRead]
    total: int
    page: int
    limit: int


class ApplicationUniversityDetail(BaseModel):
    """The university facts the student's application page prints.

    Only filled on the single-application read; the list carries just the
    monogram and logo it needs for its avatar column.
    """

    slug: str | None = None
    logo_url: str | None = None
    monogram: str | None = None
    city: str | None = None
    region: str | None = None
    kind: str | None = None
    website: str | None = None
    tagline: str | None = None
    hero_image_url: str | None = None
    rankings: list[dict[str, Any]] | None = None
    highlights: list[str] | None = None
    accommodation: dict[str, Any] | None = None
    living_cost_monthly: float | None = None


class ApplicationCourseDetail(BaseModel):
    """What the Course Details / Fees / Requirements tabs render."""

    slug: str | None = None
    qualification: str | None = None
    duration_months: int | None = None
    duration_years: float | None = None
    tuition_fee: float | None = None
    currency: str | None = None
    campus: str | None = None
    course_type: str | None = None
    intakes_summary: list[str] | None = None
    overview: str | None = None
    what_you_study: str | None = None
    modules: list[dict[str, Any]] | None = None
    skills: list[str] | None = None
    career_outcomes: list[str] | None = None
    highlights: list[str] | None = None
    requirements: dict[str, Any] | None = None
    extra_requirements: str | None = None
    minimum_ielts: float | None = None
    #: From the university's entry route: fees, deposits, criteria as the
    #: institution publishes them, verbatim.
    fee_structure: str | None = None
    scholarship_text: str | None = None
    cas_deposit: str | None = None
    enrolment_fee: str | None = None
    academic_criteria: str | None = None
    english_criteria: str | None = None
    english_waiver: str | None = None
    route_deadlines: str | None = None


class ApplicationIntakeSummary(BaseModel):
    id: UUID
    name: str
    start_date: date | None = None
    application_deadline: date | None = None


class ApplicationProgramSummary(BaseModel):
    """Just enough of `Program` (plus its `University`/`Country`) to render an
    application row without a second round trip. Built by hand in the route,
    not via `from_attributes` — `university_name` has no matching attribute
    name on `Program` to auto-map from."""

    id: UUID
    name: str
    degree_level: DegreeLevel | None = None
    intake: str | None = None
    university_id: UUID
    university_name: str
    university_country: str | None = None
    university_monogram: str | None = None
    university_logo_url: str | None = None
    university: ApplicationUniversityDetail | None = None
    course: ApplicationCourseDetail | None = None


class ApplicationCounsellorSummary(BaseModel):
    """Mirrors `AppointmentCounsellorSummary` — just enough of `User` to show
    who's handling an application, never the full staff record."""

    id: UUID
    full_name: str
    #: The student's line to their advisor — printed on the application page
    #: under "Assigned Advisor". Staff contact details, not the full record.
    phone: str | None = None
    email: str | None = None


class StudentApplicationRead(ApplicationRead):
    """`ApplicationRead` plus the program/counsellor summaries the student
    portal's application detail view renders. Not the base `ApplicationRead`
    — that schema is shared by every staff CRM route, none of which
    eager-load `program`/`counsellor`, so adding these fields there would
    turn any of them into a lazy load outside the async greenlet the moment
    it serialized a response.

    `validation_alias` points at attributes `Application` doesn't have, so
    `model_validate(application)` leaves these at their defaults instead of
    reading `application.program`/`application.counsellor` directly —
    `university_name`/`full_name` have no matching attributes to auto-map
    from those relationships, so the route builds the summaries by hand and
    assigns them afterward.
    """

    program: ApplicationProgramSummary | None = Field(default=None, validation_alias="_student_portal_program_summary")
    counsellor: ApplicationCounsellorSummary | None = Field(
        default=None, validation_alias="_student_portal_counsellor_summary"
    )
    intake: ApplicationIntakeSummary | None = Field(default=None, validation_alias="_student_portal_intake_summary")


class StudentApplicationList(BaseModel):
    """`ApplicationList` typed to carry `program` through — a plain
    `ApplicationList` would coerce each `StudentApplicationRead` down to the
    base `ApplicationRead` and silently drop it."""

    items: list[StudentApplicationRead]
    total: int
    page: int
    limit: int


class StatusRequirementRead(BaseModel):
    """What a milestone status needs, for the dialog that collects it.

    Served from `services/status_requirements.py` rather than restated in the
    client, so the form and the validator read the same config. A field added
    there appears in the dialog with no frontend change.
    """

    status: str
    prompt: str
    required_date_field: str | None
    required_document: str | None
    document_label: str
    optional_fields: list[str]
    milestone: str | None
