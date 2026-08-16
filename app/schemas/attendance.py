from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import AttendanceSource, AttendanceStatus


class AttendancePolicyUpdate(BaseModel):
    work_days: list[int] | None = None
    expected_start_time: time | None = None
    expected_end_time: time | None = None
    grace_period_minutes: int | None = None
    break_minutes: int | None = None


class AttendancePolicyRead(BaseModel):
    id: UUID
    work_days: list[int]
    expected_start_time: time
    expected_end_time: time
    grace_period_minutes: int
    break_minutes: int
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AttendanceRecordUpdate(BaseModel):
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    worked_seconds: int | None = None
    overtime_seconds: int | None = None
    status: AttendanceStatus | None = None
    notes: str | None = None


class AttendanceRecordRead(BaseModel):
    id: UUID
    user_id: UUID
    date: date
    check_in_at: datetime | None
    check_out_at: datetime | None
    worked_seconds: int | None
    overtime_seconds: int | None
    status: AttendanceStatus
    source: AttendanceSource
    recorded_by: UUID | None
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AttendanceRecordList(BaseModel):
    items: list[AttendanceRecordRead]
    total: int
    page: int
    limit: int


class AttendanceDashboardSummary(BaseModel):
    date: date
    is_work_day: bool
    present: int
    late: int
    currently_working: int
    absent: int


class AttendanceEmployeeSummary(BaseModel):
    year: int
    month: int
    present_days: int
    late_days: int
    absent_days: int
    total_worked_seconds: int
