from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DepartmentBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    manager_id: UUID | None = None


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    manager_id: UUID | None = None


class DepartmentRead(DepartmentBase):
    """ED360's version carries `organization_id`; gone with the strip (R1)."""

    id: UUID
    employee_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DepartmentList(BaseModel):
    items: list[DepartmentRead]
    total: int
    page: int
    limit: int
