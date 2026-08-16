from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import LeaveStatus


class LeaveTypeCreate(BaseModel):
    name: str
    default_days_per_year: int = 0
    paid: bool = True


class LeaveTypeUpdate(BaseModel):
    name: str | None = None
    default_days_per_year: int | None = None
    paid: bool | None = None


class LeaveTypeRead(BaseModel):
    id: UUID
    name: str
    default_days_per_year: int
    paid: bool
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class LeaveTypeList(BaseModel):
    items: list[LeaveTypeRead]


class LeaveRequestRead(BaseModel):
    id: UUID
    user_id: UUID
    leave_type_id: UUID
    start_date: date
    end_date: date
    requested_days: int
    reason: str | None
    attachment_url: str | None
    attachment_name: str | None
    status: LeaveStatus
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    review_notes: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class LeaveRequestList(BaseModel):
    items: list[LeaveRequestRead]
    total: int
    page: int
    limit: int


class LeaveApproveRequest(BaseModel):
    notes: str | None = None


class LeaveRejectRequest(BaseModel):
    reason: str


class LeaveBalanceEntry(BaseModel):
    leave_type_id: UUID
    leave_type_name: str
    allocated_days: int
    used_days: int
    remaining_days: int


class LeaveBalanceList(BaseModel):
    year: int
    items: list[LeaveBalanceEntry]
