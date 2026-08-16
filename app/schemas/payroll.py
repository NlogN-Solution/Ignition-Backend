from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import PayrollRunStatus, PayslipLineType


class SalaryStructureUpsert(BaseModel):
    basic_salary: float
    currency: str = "NPR"
    bank_name: str | None = None
    bank_account_name: str | None = None
    bank_account_number: str | None = None


class SalaryStructureRead(BaseModel):
    id: UUID
    user_id: UUID
    basic_salary: float
    currency: str
    bank_name: str | None
    bank_account_name: str | None
    bank_account_number: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PayrollRunCreate(BaseModel):
    period_year: int
    period_month: int


class PayrollRunRead(BaseModel):
    id: UUID
    period_year: int
    period_month: int
    status: PayrollRunStatus
    generated_by: UUID | None
    generated_at: datetime | None
    finalized_by: UUID | None
    finalized_at: datetime | None
    paid_by: UUID | None
    paid_at: datetime | None
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PayrollRunGenerateResult(PayrollRunRead):
    skipped_employees: int


class PayrollRunList(BaseModel):
    items: list[PayrollRunRead]
    total: int
    page: int
    limit: int


class PayslipLineItemCreate(BaseModel):
    type: PayslipLineType
    label: str
    amount: float


class PayslipLineItemRead(BaseModel):
    id: UUID
    payslip_id: UUID
    type: PayslipLineType
    label: str
    amount: float
    created_by: UUID | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PayslipRead(BaseModel):
    id: UUID
    payroll_run_id: UUID
    user_id: UUID
    basic_salary: float
    currency: str
    expected_work_days: int
    present_days: int
    paid_leave_days: int
    unpaid_days: int
    attendance_deduction: float
    additions_total: float
    deductions_total: float
    net_pay: float
    run_status: PayrollRunStatus
    run_period: str
    line_items: list[PayslipLineItemRead]
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PayslipList(BaseModel):
    items: list[PayslipRead]
