from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..api.exceptions import ConflictException, ForbiddenException, NotFoundException
from ..models import User
from ..schemas.attendance import (
    AttendanceDashboardSummary,
    AttendanceEmployeeSummary,
    AttendancePolicyRead,
    AttendancePolicyUpdate,
    AttendanceRecordList,
    AttendanceRecordRead,
    AttendanceRecordUpdate,
)
from ..services.attendance_service import AttendanceService

router = APIRouter(prefix="/attendance", tags=["Attendance"])

# Every non-student role — self-service check-in/out is a "you're staff" thing,
# not a specific-role thing.
STAFF_ROLES = (
    "admin",
    "super_admin",
    "manager",
    "counsellor",
    "staff",
    "frontdesk",
    "finance",
    "marketing",
    "support",
    "admissions",
    "viewer",
)
MANAGE_ROLES = ("admin", "super_admin", "manager")
POLICY_ROLES = ("admin", "super_admin")


async def get_attendance_service(session: AsyncSession = Depends(get_db_session)) -> AttendanceService:
    return AttendanceService(session)


# --- Policy (registered before /{record_id} so these literal paths win) ------


@router.get("/policy", response_model=AttendancePolicyRead, summary="Get the attendance policy")
async def get_policy(
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*POLICY_ROLES)),
) -> AttendancePolicyRead:
    policy = await service.get_policy()
    if policy is None:
        raise NotFoundException("No attendance policy configured yet")
    return AttendancePolicyRead.model_validate(policy)


@router.patch("/policy", response_model=AttendancePolicyRead, summary="Create or update the attendance policy")
async def update_policy(
    payload: AttendancePolicyUpdate,
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*POLICY_ROLES)),
) -> AttendancePolicyRead:
    return AttendancePolicyRead.model_validate(await service.upsert_policy(payload.model_dump(exclude_unset=True)))


# --- Self-service --------------------------------------------------------


@router.post("/check-in", response_model=AttendanceRecordRead, summary="Check in for today")
async def check_in(
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> AttendanceRecordRead:
    existing = await service.get_today(user.id)
    if existing is not None:
        detail = (
            "You've already completed attendance for today" if existing.check_out_at else "You're already checked in"
        )
        raise ConflictException(detail)
    try:
        return AttendanceRecordRead.model_validate(await service.check_in(user.id))
    except IntegrityError:
        await service.session.rollback()
        raise ConflictException("You're already checked in") from None


@router.post("/check-out", response_model=AttendanceRecordRead, summary="Check out for today")
async def check_out(
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> AttendanceRecordRead:
    existing = await service.get_today(user.id)
    if existing is None:
        raise NotFoundException("You haven't checked in today")
    if existing.check_out_at is not None:
        raise ConflictException("You've already checked out today")
    return AttendanceRecordRead.model_validate(await service.check_out(existing))


@router.get("/today", response_model=AttendanceRecordRead | None, summary="Today's attendance status")
async def get_today(
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> AttendanceRecordRead | None:
    return AttendanceRecordRead.model_validate(await service.get_today(user.id))


# --- Lists / dashboard ------------------------------------------------------


@router.get("", response_model=AttendanceRecordList, summary="List attendance records")
async def list_attendance(
    page: int = 1,
    limit: int = 20,
    user_id: UUID | None = None,
    department_id: UUID | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> AttendanceRecordList:
    if user.role not in MANAGE_ROLES:
        user_id = user.id
    records, total = await service.list_records(
        page,
        limit,
        user_id=user_id,
        department_id=department_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return AttendanceRecordList(items=records, total=total, page=page, limit=limit)


@router.get("/dashboard", response_model=AttendanceDashboardSummary, summary="Attendance dashboard summary for a date")
async def get_dashboard(
    target_date: date | None = None,
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> AttendanceDashboardSummary:
    summary = await service.dashboard_summary(target_date or date.today())
    return AttendanceDashboardSummary(**summary)


@router.get(
    "/employees/{employee_id}/summary",
    response_model=AttendanceEmployeeSummary,
    summary="An employee's attendance summary for a month",
)
async def get_employee_summary(
    employee_id: UUID,
    year: int | None = None,
    month: int | None = None,
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> AttendanceEmployeeSummary:
    if user.id != employee_id and user.role not in MANAGE_ROLES:
        raise ForbiddenException("You do not have access to this employee's attendance")
    today = date.today()
    resolved_year = year or today.year
    resolved_month = month or today.month
    summary = await service.employee_summary(employee_id, resolved_year, resolved_month)
    return AttendanceEmployeeSummary(year=resolved_year, month=resolved_month, **summary)


# --- Manual corrections (registered after the literal paths above) ---------


@router.patch("/{record_id}", response_model=AttendanceRecordRead, summary="Correct an attendance record")
async def update_attendance_record(
    record_id: UUID,
    payload: AttendanceRecordUpdate,
    service: AttendanceService = Depends(get_attendance_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> AttendanceRecordRead:
    record = await service.get_record(record_id)
    if record is None:
        raise NotFoundException("Attendance record not found")
    return AttendanceRecordRead.model_validate(
        await service.update_record(record, payload.model_dump(exclude_unset=True), recorded_by=user.id)
    )
