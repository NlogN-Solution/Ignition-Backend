from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import get_current_user, require_role
from ..api.exceptions import ForbiddenException, NotFoundException
from ..models import User
from ..models.enums import UserRole
from ..schemas.employee_profile import (
    EmployeeEmploymentEventRead,
    EmployeeProfileRead,
    EmployeeProfileUpsert,
)
from ..services.employee_profile_service import EmployeeProfileService, get_employee_profile_service

router = APIRouter(prefix="/users", tags=["Employee Profile"])

#: Who may edit anyone's employment record, and read someone else's.
MANAGE_ROLES = frozenset({UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.MANAGER})


def _assert_can_read(current_user: User, user_id: UUID, subject: str) -> None:
    if current_user.id != user_id and current_user.role not in MANAGE_ROLES:
        raise ForbiddenException(f"You do not have access to this {subject}")


@router.get("/{user_id}/employee-profile", response_model=EmployeeProfileRead, summary="Get employee profile")
async def get_employee_profile(
    user_id: UUID,
    service: EmployeeProfileService = Depends(get_employee_profile_service),
    current_user: User = Depends(get_current_user),
) -> EmployeeProfileRead:
    _assert_can_read(current_user, user_id, "employee profile")
    profile = await service.get_by_user_id(user_id)
    if profile is None:
        raise NotFoundException("Employee profile not found")
    return EmployeeProfileRead.model_validate(profile)


@router.patch(
    "/{user_id}/employee-profile",
    response_model=EmployeeProfileRead,
    summary="Create or update employee profile",
)
async def upsert_employee_profile(
    user_id: UUID,
    payload: EmployeeProfileUpsert,
    service: EmployeeProfileService = Depends(get_employee_profile_service),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
) -> EmployeeProfileRead:
    """Editing employment records is a management action even on your own row —
    designation, department and manager are not self-service fields."""
    profile = await service.upsert(
        user_id,
        payload.model_dump(exclude_unset=True),
        changed_by=current_user.id,
    )
    return EmployeeProfileRead.model_validate(profile)


@router.get(
    "/{user_id}/employee-profile/timeline",
    response_model=list[EmployeeEmploymentEventRead],
    summary="Employment lifecycle timeline",
)
async def get_employee_profile_timeline(
    user_id: UUID,
    service: EmployeeProfileService = Depends(get_employee_profile_service),
    current_user: User = Depends(get_current_user),
) -> list[EmployeeEmploymentEventRead]:
    _assert_can_read(current_user, user_id, "employee's timeline")
    profile = await service.get_by_user_id(user_id)
    if profile is None:
        raise NotFoundException("Employee profile not found")
    events = await service.list_events(profile.id)
    return [EmployeeEmploymentEventRead.model_validate(event) for event in events]
