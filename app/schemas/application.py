from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import ApplicationStatus, DegreeLevel


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
    tuition_fee: float | None = None
    scholarship_amount: float | None = None
    university_application_id: str | None = None
    intake_id: UUID | None = None
    remarks: str | None = None


class ApplicationCreate(ApplicationBase):
    pass


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
    tuition_fee: float | None = None
    scholarship_amount: float | None = None
    university_application_id: str | None = None
    intake_id: UUID | None = None
    remarks: str | None = None


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


class ApplicationCounsellorSummary(BaseModel):
    """Mirrors `AppointmentCounsellorSummary` — just enough of `User` to show
    who's handling an application, never the full staff record."""

    id: UUID
    full_name: str


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


class StudentApplicationList(BaseModel):
    """`ApplicationList` typed to carry `program` through — a plain
    `ApplicationList` would coerce each `StudentApplicationRead` down to the
    base `ApplicationRead` and silently drop it."""

    items: list[StudentApplicationRead]
    total: int
    page: int
    limit: int
