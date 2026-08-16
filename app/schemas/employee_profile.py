from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import EmploymentEventType, EmploymentType


class EmployeeProfileUpsert(BaseModel):
    employee_code: str | None = None
    department: str | None = None
    department_id: UUID | None = None
    designation: str | None = None
    joining_date: date | None = None
    employment_status: str | None = None
    employment_type: EmploymentType | None = None
    office_location: str | None = None
    probation_end_date: date | None = None
    contract_start_date: date | None = None
    contract_end_date: date | None = None
    manager_id: UUID | None = None


class EmployeeProfileRead(BaseModel):
    id: UUID
    user_id: UUID
    employee_code: str | None
    department: str | None
    department_id: UUID | None
    designation: str | None
    joining_date: date | None
    employment_status: str | None
    employment_type: EmploymentType | None
    office_location: str | None
    probation_end_date: date | None
    contract_start_date: date | None
    contract_end_date: date | None
    manager_id: UUID | None
    #: Resolved display name — `department_ref.name` when `department_id` is
    #: set, else the legacy free-text `department` string.
    department_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EmployeeEmploymentEventRead(BaseModel):
    id: UUID
    employee_profile_id: UUID
    event_type: EmploymentEventType
    description: str | None
    changed_by: UUID | None
    previous_value: str | None
    new_value: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
