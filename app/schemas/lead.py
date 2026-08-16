from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import ConversionSource, FollowUpMethod, FollowUpOutcome, LeadPriority, LeadStatus, LostReason


class LeadBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = None
    last_name: str | None = None
    email: str | None = Field(default=None, min_length=3)
    phone: str = Field(min_length=7, max_length=20)
    source: str | None = None
    status: str | None = None
    priority: LeadPriority | None = None
    interested_country: str | None = None
    interested_course: str | None = None
    preferred_intake: str | None = None
    tags: list[str] | None = None
    assigned_to: UUID | None = None
    remarks: str | None = None
    next_follow_up_at: datetime | None = None


class LeadCreate(LeadBase):
    source: str | None = Field(default="website")
    status: str | None = Field(default="new")
    priority: LeadPriority | None = Field(default=LeadPriority.WARM)


class LeadUpdate(BaseModel):
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str | None = None
    status: str | None = None
    priority: LeadPriority | None = None
    interested_country: str | None = None
    interested_course: str | None = None
    preferred_intake: str | None = None
    tags: list[str] | None = None
    assigned_to: UUID | None = None
    remarks: str | None = None
    next_follow_up_at: datetime | None = None


class LeadAssign(BaseModel):
    assigned_to: UUID


class LeadConvert(BaseModel):
    converted_user_id: UUID | None = None
    conversion_source: ConversionSource | None = None
    remarks: str | None = None
    create_portal_account: bool = False


class LeadStatusUpdate(BaseModel):
    """`status` is the enum, not a free string — ED360 types it as `str` and
    assigns it straight to the enum column, so an unknown value reaches the
    database as an invalid-enum error rather than a 422."""

    status: LeadStatus
    remarks: str | None = None


class LeadQualify(BaseModel):
    remarks: str | None = None


class LeadMarkLost(BaseModel):
    reason: LostReason
    remarks: str | None = None


class LeadRead(LeadBase):
    id: UUID
    converted_user_id: UUID | None = None
    qualified_by: UUID | None = None
    qualified_at: datetime | None = None
    converted_by: UUID | None = None
    converted_at: datetime | None = None
    conversion_source: ConversionSource | None = None
    lost_reason: LostReason | None = None
    lost_at: datetime | None = None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class LeadConvertResult(BaseModel):
    lead: LeadRead
    created_new_user: bool
    student_user_id: UUID | None
    portal_account_created: bool = False
    generated_password: str | None = None


class LeadList(BaseModel):
    items: list[LeadRead]
    total: int
    page: int
    limit: int


class LeadActivityRead(BaseModel):
    id: UUID
    lead_id: UUID
    activity_type: str
    performed_by: UUID | None
    title: str | None
    description: str | None
    old_status: str | None
    new_status: str | None
    due_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Follow-ups ---------------------------------------------------------


class LeadFollowUpCreate(BaseModel):
    scheduled_at: datetime
    method: FollowUpMethod
    counsellor_id: UUID | None = None
    notes: str | None = None


class LeadFollowUpComplete(BaseModel):
    outcome: FollowUpOutcome
    notes: str | None = None
    next_follow_up_at: datetime | None = None
    completed_at: datetime | None = None


class LeadFollowUpRead(BaseModel):
    id: UUID
    lead_id: UUID
    attempt_number: int
    scheduled_at: datetime
    completed_at: datetime | None
    counsellor_id: UUID | None
    method: FollowUpMethod
    outcome: FollowUpOutcome | None
    notes: str | None
    next_follow_up_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeadFollowUpList(BaseModel):
    items: list[LeadFollowUpRead]
    total: int


class DueFollowUpItem(BaseModel):
    id: UUID
    lead_id: UUID
    lead_name: str
    lead_priority: LeadPriority
    assigned_to: UUID | None
    attempt_number: int
    scheduled_at: datetime
    method: FollowUpMethod
    counsellor_id: UUID | None


class DueFollowUpList(BaseModel):
    items: list[DueFollowUpItem]
    total: int
    page: int
    limit: int
