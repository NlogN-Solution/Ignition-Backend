from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from ..models import User
from ..models.enums import PayrollRunStatus
from ..schemas.payroll import (
    PayrollRunCreate,
    PayrollRunGenerateResult,
    PayrollRunList,
    PayrollRunRead,
    PayslipLineItemCreate,
    PayslipList,
    PayslipRead,
    SalaryStructureRead,
    SalaryStructureUpsert,
)
from ..services.payroll_service import PayrollService

router = APIRouter(tags=["Payroll"])

# Broad self-service tier — same list as Attendance/Leave — lets any staff
# member reach their own payslip. Actually processing payroll (money) is
# tighter: MANAGE_ROLES only, with VIEW_ROLES getting read-only visibility
# across the team (deliberately excludes manager from MANAGE_ROLES, unlike
# Attendance/Leave's approval tier — this is the one domain that moves money).
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
)
VIEW_ROLES = ("admin", "super_admin", "manager")
MANAGE_ROLES = ("admin", "super_admin")


async def get_payroll_service(session: AsyncSession = Depends(get_db_session)) -> PayrollService:
    return PayrollService(session)


# --- Salary structures ---------------------------------------------------------


@router.get(
    "/salary-structures/{user_id}", response_model=SalaryStructureRead, summary="Get an employee's salary structure"
)
async def get_salary_structure(
    user_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*VIEW_ROLES)),
) -> SalaryStructureRead:
    structure = await service.get_salary_structure(user_id)
    if structure is None:
        raise NotFoundException("No salary structure set for this employee")
    return SalaryStructureRead.model_validate(structure)


@router.put(
    "/salary-structures/{user_id}", response_model=SalaryStructureRead, summary="Set an employee's salary structure"
)
async def upsert_salary_structure(
    user_id: UUID,
    payload: SalaryStructureUpsert,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> SalaryStructureRead:
    try:
        return SalaryStructureRead.model_validate(
            await service.upsert_salary_structure(user_id, payload.model_dump(), user.id)
        )
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc


# --- Payroll runs ----------------------------------------------------------------


@router.get("/payroll-runs", response_model=PayrollRunList, summary="List payroll runs")
async def list_payroll_runs(
    page: int = 1,
    limit: int = 20,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*VIEW_ROLES)),
) -> PayrollRunList:
    runs, total = await service.list_runs(page, limit)
    return PayrollRunList(items=runs, total=total, page=page, limit=limit)


@router.post("/payroll-runs", response_model=PayrollRunGenerateResult, summary="Generate a payroll run for a period")
async def create_payroll_run(
    payload: PayrollRunCreate,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> PayrollRunGenerateResult:
    existing = await service.get_run_by_period(payload.period_year, payload.period_month)
    if existing is not None:
        raise ConflictException("A payroll run already exists for this period")
    run, skipped = await service.create_run(payload.period_year, payload.period_month, user.id)
    return PayrollRunGenerateResult(**PayrollRunRead.model_validate(run).model_dump(), skipped_employees=skipped)


@router.get("/payroll-runs/{run_id}", response_model=PayrollRunRead, summary="Get a payroll run")
async def get_payroll_run(
    run_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*VIEW_ROLES)),
) -> PayrollRunRead:
    run = await service.get_run(run_id)
    if run is None:
        raise NotFoundException("Payroll run not found")
    return PayrollRunRead.model_validate(run)


@router.post("/payroll-runs/{run_id}/finalize", response_model=PayrollRunRead, summary="Finalize a payroll run")
async def finalize_payroll_run(
    run_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> PayrollRunRead:
    run = await service.get_run(run_id)
    if run is None:
        raise NotFoundException("Payroll run not found")
    if run.status != PayrollRunStatus.DRAFT:
        raise ConflictException(f"This run is already {run.status.value}")
    return PayrollRunRead.model_validate(await service.finalize_run(run, user.id))


@router.post("/payroll-runs/{run_id}/mark-paid", response_model=PayrollRunRead, summary="Mark a payroll run as paid")
async def mark_payroll_run_paid(
    run_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> PayrollRunRead:
    run = await service.get_run(run_id)
    if run is None:
        raise NotFoundException("Payroll run not found")
    if run.status != PayrollRunStatus.FINALIZED:
        raise ConflictException(f"This run is {run.status.value}, must be finalized first")
    return PayrollRunRead.model_validate(await service.mark_paid(run, user.id))


@router.get("/payroll-runs/{run_id}/payslips", response_model=PayslipList, summary="List a payroll run's payslips")
async def list_run_payslips(
    run_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*VIEW_ROLES)),
) -> PayslipList:
    run = await service.get_run(run_id)
    if run is None:
        raise NotFoundException("Payroll run not found")
    payslips = await service.list_payslips(run_id)
    return PayslipList(items=payslips)


# --- Payslips ----------------------------------------------------------------


@router.get("/payslips/{payslip_id}", response_model=PayslipRead, summary="Get a payslip")
async def get_payslip(
    payslip_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> PayslipRead:
    payslip = await service.get_payslip(payslip_id)
    if payslip is None:
        raise NotFoundException("Payslip not found")
    if user.id != payslip.user_id and user.role not in VIEW_ROLES:
        raise ForbiddenException("You do not have access to this payslip")
    return PayslipRead.model_validate(payslip)


@router.post("/payslips/{payslip_id}/line-items", response_model=PayslipRead, summary="Add a line item to a payslip")
async def add_payslip_line_item(
    payslip_id: UUID,
    payload: PayslipLineItemCreate,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> PayslipRead:
    payslip = await service.get_payslip(payslip_id)
    if payslip is None:
        raise NotFoundException("Payslip not found")
    if payslip.run_status != PayrollRunStatus.DRAFT:
        raise ConflictException("Line items can only be added while the run is a draft")
    await service.add_line_item(payslip, payload.type, payload.label, payload.amount, user.id)
    refreshed = await service.get_payslip(payslip_id)
    assert refreshed is not None
    return PayslipRead.model_validate(refreshed)


@router.delete(
    "/payslips/{payslip_id}/line-items/{item_id}", response_model=PayslipRead, summary="Remove a payslip line item"
)
async def remove_payslip_line_item(
    payslip_id: UUID,
    item_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> PayslipRead:
    payslip = await service.get_payslip(payslip_id)
    if payslip is None:
        raise NotFoundException("Payslip not found")
    if payslip.run_status != PayrollRunStatus.DRAFT:
        raise ConflictException("Line items can only be removed while the run is a draft")
    item = next((i for i in payslip.line_items if i.id == item_id), None)
    if item is None:
        raise NotFoundException("Line item not found")
    await service.remove_line_item(payslip, item)
    refreshed = await service.get_payslip(payslip_id)
    assert refreshed is not None
    return PayslipRead.model_validate(refreshed)


@router.get("/users/{user_id}/payslips", response_model=PayslipList, summary="An employee's payslip history")
async def get_employee_payslip_history(
    user_id: UUID,
    service: PayrollService = Depends(get_payroll_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> PayslipList:
    if user.id != user_id and user.role not in VIEW_ROLES:
        raise ForbiddenException("You do not have access to this employee's payslips")
    payslips = await service.get_employee_payslip_history(user_id)
    return PayslipList(items=payslips)
