from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from ..models.enums import EmploymentType, UserRole, UserStatus


class EmployeeDirectoryEntry(BaseModel):
    """One row per staff user (role != student).

    Left-joined against EmployeeProfile and Department, so a staff account with
    no employee profile filled in yet still appears — with those fields null —
    rather than being hidden from the directory.
    """

    id: UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None
    avatar_url: str | None
    role: UserRole
    status: UserStatus
    employee_code: str | None
    designation: str | None
    department_id: UUID | None
    department_name: str | None
    employment_status: str | None
    employment_type: EmploymentType | None
    joining_date: date | None


class EmployeeDirectoryList(BaseModel):
    items: list[EmployeeDirectoryEntry]
    total: int
    page: int
    limit: int
