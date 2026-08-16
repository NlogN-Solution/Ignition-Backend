from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role
from ..models import User
from ..models.enums import UserRole
from ..schemas.employees import EmployeeDirectoryEntry, EmployeeDirectoryList
from ..services.employee_profile_service import EmployeeProfileService, get_employee_profile_service

router = APIRouter(prefix="/employees", tags=["People Directory"])


@router.get("", response_model=EmployeeDirectoryList, summary="List staff directory (People > Directory)")
async def list_employees(
    page: int = 1,
    limit: int = 24,
    search: str | None = None,
    department_id: UUID | None = None,
    employment_status: str | None = None,
    service: EmployeeProfileService = Depends(get_employee_profile_service),
    # Browsing the full roster with contact details and employment status is a
    # manager-and-up capability. An individual staff member reads their own row
    # via GET /users/{id}/employee-profile, and everyone can resolve a
    # colleague's name through /users/staff-directory.
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
) -> EmployeeDirectoryList:
    rows, total = await service.list_directory(
        page,
        limit,
        search=search,
        department_id=department_id,
        employment_status=employment_status,
    )
    return EmployeeDirectoryList(
        items=[EmployeeDirectoryEntry(**dict(row)) for row in rows],
        total=total,
        page=page,
        limit=limit,
    )
