from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VisaStageRead(BaseModel):
    id: UUID
    label: str
    description: str | None = None
    #: Named `stage_date` rather than `date`: a field called `date` with a
    #: default value shadows the `date` type within this class's own
    #: namespace, and pydantic fails to resolve `date | None` against a
    #: `from __future__ import annotations` forward reference when that
    #: happens. The wire shape keeps the plain `date` key via the aliases.
    stage_date: date | None = Field(default=None, validation_alias="date", serialization_alias="date")
    state: str
    order: int

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class VisaAppointmentRead(BaseModel):
    id: UUID
    title: str
    provider: str | None = None
    scheduled_at: datetime | None = None
    status: str
    note: str | None = None

    model_config = ConfigDict(from_attributes=True)


class VisaDocumentRequirementRead(BaseModel):
    id: UUID
    title: str
    status: str
    note: str | None = None
    order: int

    model_config = ConfigDict(from_attributes=True)


class VisaFeeRead(BaseModel):
    id: UUID
    label: str
    amount_usd: Decimal
    due_date: date | None = None
    paid: bool
    paid_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DepartureChecklistItemRead(BaseModel):
    id: UUID
    title: str
    is_complete: bool = False
    completed_at: datetime | None = None
    order: int

    model_config = ConfigDict(from_attributes=True)


class VisaCaseRead(BaseModel):
    id: UUID
    application_id: UUID
    destination_country: str
    visa_type: str
    application_number: str | None = None
    status: str
    target_lodgement_date: date | None = None
    programme_start: date | None = None
    processing_estimate_weeks: int | None = None
    stages: list[VisaStageRead] = Field(default_factory=list)
    appointments: list[VisaAppointmentRead] = Field(default_factory=list)
    required_documents: list[VisaDocumentRequirementRead] = Field(
        default_factory=list, validation_alias="document_requirements"
    )
    fees: list[VisaFeeRead] = Field(default_factory=list)
    departure_checklist: list[DepartureChecklistItemRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class VisaAppointmentBook(BaseModel):
    scheduled_at: datetime


class VisaFeeUpdate(BaseModel):
    paid: bool


class DepartureChecklistItemUpdate(BaseModel):
    completed: bool
