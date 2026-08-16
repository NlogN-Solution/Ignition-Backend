from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AppointmentStatus, AppointmentType


class AppointmentBase(BaseModel):
    student_id: UUID | None = None
    counsellor_id: UUID | None = None
    attendee_ids: list[UUID] | None = None
    lead_id: UUID | None = None
    appointment_type: AppointmentType
    status: AppointmentStatus | None = None
    title: str
    description: str | None = None
    preferred_date: date | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    meeting_link: str | None = None
    notes: str | None = None
    created_by: UUID | None = None


class AppointmentCreate(AppointmentBase):
    pass


class AppointmentUpdate(BaseModel):
    student_id: UUID | None = None
    counsellor_id: UUID | None = None
    attendee_ids: list[UUID] | None = None
    lead_id: UUID | None = None
    appointment_type: AppointmentType | None = None
    status: AppointmentStatus | None = None
    title: str | None = None
    description: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    meeting_link: str | None = None
    notes: str | None = None
    created_by: UUID | None = None


class AppointmentRead(AppointmentBase):
    id: UUID
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AppointmentList(BaseModel):
    items: list[AppointmentRead]
    total: int
    page: int
    limit: int


class AppointmentCounsellorSummary(BaseModel):
    id: UUID
    full_name: str


class StudentAppointmentRead(AppointmentRead):
    """`AppointmentRead` plus the counsellor's display name — same reasoning,
    and same `validation_alias` trick to skip it, as
    `StudentApplicationRead.program` in `schemas/application.py`."""

    counsellor: AppointmentCounsellorSummary | None = Field(
        default=None, validation_alias="_student_portal_counsellor_summary"
    )


class StudentAppointmentList(BaseModel):
    items: list[StudentAppointmentRead]
    total: int
    page: int
    limit: int


class AppointmentRequestCreate(BaseModel):
    """What a student may say when asking for a slot.

    Deliberately not `AppointmentCreate`: time, location, meeting link, status
    and attendees are the counsellor's to set on confirmation, so they are not
    accepted here at all. `preferred_date` is a real `date`, not a string —
    passing the raw body through untyped sent it to a DATE column as text.
    """

    title: str = Field(min_length=1, max_length=200)
    preferred_date: date
    appointment_type: AppointmentType = AppointmentType.CONSULTATION
    description: str | None = None
