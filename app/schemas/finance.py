from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FundingSourceRead(BaseModel):
    id: UUID
    source_type: str
    provider: str | None = None
    amount_usd: Decimal
    status: str
    verification: str
    proof_document_id: UUID | None = None
    remarks: str | None = None

    model_config = ConfigDict(from_attributes=True)


class FundingSourceCreate(BaseModel):
    source_type: str = Field(min_length=1, max_length=80)
    provider: str | None = None
    amount_usd: Decimal = Field(gt=0)
    remarks: str | None = None


class FundingSourceUpdate(BaseModel):
    provider: str | None = None
    amount_usd: Decimal | None = Field(default=None, gt=0)
    remarks: str | None = None


class LoanDocumentRead(BaseModel):
    id: UUID
    name: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class LoanDisbursementRead(BaseModel):
    id: UUID
    label: str
    amount: Decimal
    disbursement_date: date | None = None
    status: str

    model_config = ConfigDict(from_attributes=True)


class LoanRead(BaseModel):
    id: UUID
    provider: str
    product_name: str | None = None
    approved_amount: Decimal
    disbursed_amount: Decimal
    interest_rate: Decimal | None = None
    processing_status: str
    sanctioned_at: date | None = None
    expected_full_disbursement: date | None = None
    repayment_starts_at: date | None = None
    tenure_months: int | None = None
    monthly_repayment: Decimal | None = None
    collateral: str | None = None
    documents: list[LoanDocumentRead] = Field(default_factory=list)
    disbursements: list[LoanDisbursementRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BudgetCategoryRead(BaseModel):
    category: str
    amount: Decimal
    essential: bool

    model_config = ConfigDict(from_attributes=True)


class BudgetRead(BaseModel):
    planned_monthly_income: Decimal | None = None
    categories: list[BudgetCategoryRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BudgetCategoryInput(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(ge=0)
    essential: bool = True


class BudgetReplace(BaseModel):
    planned_monthly_income: Decimal | None = Field(default=None, ge=0)
    categories: list[BudgetCategoryInput] = Field(default_factory=list)


class SavingsGoalRead(BaseModel):
    kind: str
    current_savings: Decimal
    monthly_contribution: Decimal | None = None
    target_date: date | None = None
    recommended_months: int | None = None

    model_config = ConfigDict(from_attributes=True)


class SavingsGoalUpsert(BaseModel):
    current_savings: Decimal = Field(ge=0)
    monthly_contribution: Decimal | None = Field(default=None, ge=0)
    target_date: date | None = None
    recommended_months: int | None = Field(default=None, ge=0)


class CostOfLivingCategoryRead(BaseModel):
    category: str
    amount_usd: Decimal
    essential: bool
    phase: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CountryCostOfLivingRead(BaseModel):
    proof_of_funds_name: str | None = None
    note: str | None = None
    tuition_usd: Decimal | None = None
    living_cost_usd: Decimal | None = None
    insurance_usd: Decimal | None = None
    visa_fee_usd: Decimal | None = None
    biometrics_usd: Decimal | None = None
    cost_categories: list[CostOfLivingCategoryRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CurrencyRateRead(BaseModel):
    code: str
    name: str
    symbol: str | None = None
    per_usd: Decimal
    change_pct: Decimal | None = None
    trend: list[float] | None = None
    updated_at_source: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
