"""The application journey: one read model for both portals, and its actions.

`JourneyRead` is deliberately the whole journey in one response — steps,
their checklist items, review rounds and interview slots — because both
screens draw all ten stages at once and a stepper that fetches per stage
shows a different truth in each row while it loads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models.enums import (
    ChecklistItemStatus,
    DocumentType,
    StepSlotOutcome,
    StepSlotStatus,
    StepSubmissionStatus,
    WorkflowStageKind,
    WorkflowStepStatus,
)


class JourneyChecklistItemRead(BaseModel):
    id: UUID
    document_type: DocumentType | None
    custom_label: str | None
    is_required: bool
    status: ChecklistItemStatus
    document_id: UUID | None
    notes: str | None

    model_config = ConfigDict(from_attributes=True)


class JourneySubmissionRead(BaseModel):
    id: UUID
    round: int
    body_text: str | None
    document_id: UUID | None
    document_name: str | None = None
    external_url: str | None
    status: StepSubmissionStatus
    feedback: str | None
    feedback_document_ids: list[UUID] = []
    submitted_by: UUID | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("feedback_document_ids", mode="before")
    @classmethod
    def _none_is_empty(cls, value: list[UUID] | None) -> list[UUID]:
        return list(value or [])


class JourneySlotRead(BaseModel):
    id: UUID
    attempt: int
    starts_at: datetime
    ends_at: datetime | None
    location: str | None
    meeting_link: str | None
    status: StepSlotStatus
    outcome: StepSlotOutcome | None
    outcome_note: str | None
    appointment_id: UUID | None
    booked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class JourneyStepRead(BaseModel):
    id: UUID
    stage_id: UUID | None
    key: str | None
    name: str
    description: str | None
    kind: WorkflowStageKind
    config: dict[str, Any]
    status: WorkflowStepStatus
    order: int
    started_at: datetime | None
    completed_at: datetime | None
    progress: dict[str, Any]
    checklist: list[JourneyChecklistItemRead] = []
    submissions: list[JourneySubmissionRead] = []
    slots: list[JourneySlotRead] = []
    #: What the step is waiting on, for badges: `student`, `staff`, or null.
    waiting_on: Literal["student", "staff"] | None = None
    #: Staff-only. Null for students.
    notes: str | None = None
    assigned_to: UUID | None = None


class JourneyRead(BaseModel):
    application_id: UUID
    workflow_id: UUID
    template_id: UUID
    template_name: str
    status: str
    study_level: Literal["ug", "pg"]
    has_study_gap: bool
    current_step_id: UUID | None
    progress_percent: int
    steps: list[JourneyStepRead]


# --- actions ---------------------------------------------------------------------


class StudyGapUpdate(BaseModel):
    has_study_gap: bool


class SubmissionCreate(BaseModel):
    body_text: str | None = Field(None, max_length=20000)
    document_id: UUID | None = None
    external_url: str | None = Field(None, max_length=2000)

    @field_validator("external_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not value.lower().startswith("https://"):
            raise ValueError("Links must start with https://")
        return value

    @field_validator("body_text")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None

    @model_validator(mode="after")
    def _something(self) -> SubmissionCreate:
        if not (self.body_text or self.document_id or self.external_url):
            raise ValueError("Write your answers, attach a file, or add a link")
        return self


class SubmissionReview(BaseModel):
    verdict: Literal["verified", "changes_requested"]
    feedback: str | None = Field(None, max_length=20000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _feedback_when_sending_back(self) -> SubmissionReview:
        if self.verdict == "changes_requested" and not (self.feedback and self.feedback.strip()):
            raise ValueError("Tell the student what to change")
        return self


class SlotCreate(BaseModel):
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = Field(None, max_length=255)
    meeting_link: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _ends_after_start(self) -> SlotCreate:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("A slot must end after it starts")
        return self


class SlotsPublish(BaseModel):
    slots: list[SlotCreate] = Field(..., min_length=1, max_length=12)


class SlotOutcome(BaseModel):
    outcome: StepSlotOutcome
    note: str | None = Field(None, max_length=5000)


class TaskTick(BaseModel):
    done: bool


class SwitchTemplateRequest(BaseModel):
    template_id: UUID | None = None
