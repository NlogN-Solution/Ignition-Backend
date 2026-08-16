from __future__ import annotations

import calendar
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AttendanceRecord,
    EmployeeEmploymentEvent,
    EmployeeProfile,
    LeaveRequest,
    LeaveType,
    PayrollRun,
    Payslip,
    PayslipLineItem,
    SalaryStructure,
)
from ..models.enums import AttendanceStatus, EmploymentEventType, LeaveStatus, PayrollRunStatus, PayslipLineType
from .attendance_service import DEFAULT_WORK_DAYS, AttendanceService, work_day_dates


class PayrollService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.attendance_service = AttendanceService(session)

    # --- Salary structures ---------------------------------------------------

    async def get_salary_structure(self, user_id: UUID) -> SalaryStructure | None:
        query = select(SalaryStructure).where(SalaryStructure.user_id == user_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def upsert_salary_structure(
        self, user_id: UUID, data: dict[str, Any], changed_by: UUID | None
    ) -> SalaryStructure:
        profile = (
            await self.session.execute(select(EmployeeProfile).where(EmployeeProfile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            raise ValueError("An employee profile must exist before a salary can be set")

        structure = await self.get_salary_structure(user_id)
        previous_salary = structure.basic_salary if structure is not None else None

        if structure is None:
            structure = SalaryStructure(user_id=user_id, **data)
            self.session.add(structure)
        else:
            for key, value in data.items():
                if value is not None:
                    setattr(structure, key, value)

        new_salary = data.get("basic_salary")
        if new_salary is not None and (previous_salary is None or float(previous_salary) != float(new_salary)):
            self.session.add(
                EmployeeEmploymentEvent(
                    employee_profile_id=profile.id,
                    event_type=EmploymentEventType.SALARY_CHANGED,
                    changed_by=changed_by,
                    previous_value=str(previous_salary) if previous_salary is not None else None,
                    new_value=str(new_salary),
                )
            )

        await self.session.commit()
        await self.session.refresh(structure)
        return structure

    # --- Payroll runs ----------------------------------------------------------

    async def get_run(self, run_id: UUID) -> PayrollRun | None:
        query = select(PayrollRun).where(PayrollRun.id == run_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_run_by_period(self, year: int, month: int) -> PayrollRun | None:
        result = await self.session.execute(
            select(PayrollRun).where(
                PayrollRun.period_year == year,
                PayrollRun.period_month == month,
            )
        )
        return result.scalar_one_or_none()

    async def list_runs(self, page: int, limit: int) -> tuple[list[PayrollRun], int]:
        base = select(PayrollRun).where()
        total = await self.session.scalar(select(func.count()).select_from(PayrollRun).where()) or 0
        query = (
            base.order_by(PayrollRun.period_year.desc(), PayrollRun.period_month.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_run(self, year: int, month: int, generated_by: UUID) -> tuple[PayrollRun, int]:
        """Generates one Payslip per employee who has a SalaryStructure, computing
        paid/unpaid days from real Attendance + Leave data for the period. Returns
        (run, skipped_count) — employees with a profile but no salary structure are
        left out and counted, not silently guessed at."""
        period_start = date(year, month, 1)
        period_end = date(year, month, calendar.monthrange(year, month)[1])

        policy = await self.attendance_service.get_policy()
        work_days = set(policy.work_days) if policy is not None else set(DEFAULT_WORK_DAYS)
        expected_work_days = len(work_day_dates(period_start, period_end, work_days))

        run = PayrollRun(
            period_year=year,
            period_month=month,
            status=PayrollRunStatus.DRAFT,
            generated_by=generated_by,
            generated_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()

        structures = list((await self.session.execute(select(SalaryStructure).where())).scalars().all())
        profile_count = await self.session.scalar(select(func.count()).select_from(EmployeeProfile).where()) or 0
        skipped = max(0, profile_count - len(structures))

        for structure in structures:
            present_days = await self._count_present_days(structure.user_id, period_start, period_end)
            paid_leave_days = await self._count_paid_leave_days(structure.user_id, period_start, period_end, work_days)
            unpaid_days = max(0, expected_work_days - present_days - paid_leave_days)

            basic_salary = float(structure.basic_salary)
            attendance_deduction = (
                round((basic_salary / expected_work_days) * unpaid_days, 2) if expected_work_days > 0 else 0.0
            )
            net_pay = round(basic_salary - attendance_deduction, 2)

            self.session.add(
                Payslip(
                    payroll_run_id=run.id,
                    user_id=structure.user_id,
                    basic_salary=structure.basic_salary,
                    currency=structure.currency,
                    expected_work_days=expected_work_days,
                    present_days=present_days,
                    paid_leave_days=paid_leave_days,
                    unpaid_days=unpaid_days,
                    attendance_deduction=attendance_deduction,
                    additions_total=0,
                    deductions_total=0,
                    net_pay=net_pay,
                )
            )

        await self.session.commit()
        await self.session.refresh(run)
        return run, skipped

    async def _count_present_days(self, user_id: UUID, period_start: date, period_end: date) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(AttendanceRecord)
            .where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.date >= period_start,
                AttendanceRecord.date <= period_end,
                AttendanceRecord.status.in_([AttendanceStatus.PRESENT, AttendanceStatus.LATE]),
            )
        )
        return count or 0

    async def _count_paid_leave_days(
        self,
        user_id: UUID,
        period_start: date,
        period_end: date,
        work_days: set[int],
    ) -> int:
        result = await self.session.execute(
            select(LeaveRequest, LeaveType.paid)
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .where(
                LeaveRequest.user_id == user_id,
                LeaveRequest.status == LeaveStatus.APPROVED,
                LeaveRequest.start_date <= period_end,
                LeaveRequest.end_date >= period_start,
            )
        )
        total_paid_days = 0
        for leave_request, is_paid in result.all():
            if not is_paid:
                continue
            overlap_start = max(leave_request.start_date, period_start)
            overlap_end = min(leave_request.end_date, period_end)
            if overlap_start > overlap_end:
                continue
            total_paid_days += len(work_day_dates(overlap_start, overlap_end, work_days))
        return total_paid_days

    async def finalize_run(self, run: PayrollRun, finalized_by: UUID) -> PayrollRun:
        run.status = PayrollRunStatus.FINALIZED
        run.finalized_by = finalized_by
        run.finalized_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def mark_paid(self, run: PayrollRun, paid_by: UUID) -> PayrollRun:
        run.status = PayrollRunStatus.PAID
        run.paid_by = paid_by
        run.paid_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    # --- Payslips ----------------------------------------------------------------

    async def get_payslip(self, payslip_id: UUID) -> Payslip | None:
        query = (
            select(Payslip)
            .options(selectinload(Payslip.line_items), selectinload(Payslip.payroll_run))
            .where(Payslip.id == payslip_id)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_payslips(self, run_id: UUID) -> list[Payslip]:
        query = (
            select(Payslip)
            .options(selectinload(Payslip.line_items), selectinload(Payslip.payroll_run))
            .where(Payslip.payroll_run_id == run_id)
            .order_by(Payslip.created_at)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_employee_payslip_history(self, user_id: UUID) -> list[Payslip]:
        query = (
            select(Payslip)
            .options(selectinload(Payslip.line_items), selectinload(Payslip.payroll_run))
            .where(Payslip.user_id == user_id)
            .order_by(Payslip.created_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    # --- Line items ----------------------------------------------------------------

    async def add_line_item(
        self, payslip: Payslip, type_: PayslipLineType, label: str, amount: float, created_by: UUID
    ) -> PayslipLineItem:
        item = PayslipLineItem(
            payslip_id=payslip.id,
            type=type_,
            label=label,
            amount=amount,
            created_by=created_by,
        )
        self.session.add(item)
        await self.session.flush()
        await self._recompute_totals(payslip)
        await self.session.commit()
        await self.session.refresh(payslip)
        return item

    async def remove_line_item(self, payslip: Payslip, item: PayslipLineItem) -> None:
        await self.session.delete(item)
        await self.session.flush()
        await self._recompute_totals(payslip)
        await self.session.commit()

    async def _recompute_totals(self, payslip: Payslip) -> None:
        result = await self.session.execute(
            select(PayslipLineItem.type, func.coalesce(func.sum(PayslipLineItem.amount), 0))
            .where(PayslipLineItem.payslip_id == payslip.id)
            .group_by(PayslipLineItem.type)
        )
        totals: dict[PayslipLineType, float] = dict(result.all())  # type: ignore[arg-type]
        additions = float(totals.get(PayslipLineType.ADDITION, 0))
        deductions = float(totals.get(PayslipLineType.DEDUCTION, 0))
        payslip.additions_total = additions
        payslip.deductions_total = deductions
        payslip.net_pay = round(
            float(payslip.basic_salary) - float(payslip.attendance_deduction) + additions - deductions, 2
        )
