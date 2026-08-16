"""The student's own funding picture.

Phase 6. Almost everything here is student-writable, because it is the
student's own plan rather than a fact the platform observed — unlike
`StudentLoan`, which staff or the lender maintain and the student only reads.

Real money that moves through the platform is `Payment`
(`/student/me/payments`, Phase 5a already covers it); nothing here creates a
`Payment` row.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException, NotFoundException
from ..models import (
    BudgetCategory,
    CountryCostOfLiving,
    CurrencyRate,
    StudentBudget,
    StudentFundingSource,
    StudentLoan,
    StudentSavingsGoal,
    User,
)
from ..models.enums import FundingVerificationStatus, SavingsGoalKind


class FundingSourceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for(self, student: User) -> list[StudentFundingSource]:
        result = await self.session.scalars(
            select(StudentFundingSource)
            .where(StudentFundingSource.student_id == student.id)
            .order_by(StudentFundingSource.created_at)
        )
        return list(result)

    async def _owned(self, student: User, source_id: UUID) -> StudentFundingSource:
        source = await self.session.scalar(
            select(StudentFundingSource).where(
                StudentFundingSource.id == source_id, StudentFundingSource.student_id == student.id
            )
        )
        if source is None:
            raise NotFoundException("Funding source not found")
        return source

    async def create(
        self, student: User, source_type: str, provider: str | None, amount_usd: Decimal, remarks: str | None
    ) -> StudentFundingSource:
        source = StudentFundingSource(
            student_id=student.id, source_type=source_type, provider=provider, amount_usd=amount_usd, remarks=remarks
        )
        self.session.add(source)
        await self.session.commit()
        await self.session.refresh(source)
        return source

    async def update(
        self,
        student: User,
        source_id: UUID,
        *,
        provider: str | None = None,
        amount_usd: Decimal | None = None,
        remarks: str | None = None,
    ) -> StudentFundingSource:
        """Editable up to the point staff verify it. A verified funding source
        is on record for the visa application — the student rewriting the
        amount under staff's feet would silently invalidate what was already
        relied on, so verified rows must be withdrawn and re-added instead."""
        source = await self._owned(student, source_id)
        if source.verification is FundingVerificationStatus.VERIFIED:
            raise BadRequestException("This funding source is verified and can no longer be edited.")
        if provider is not None:
            source.provider = provider
        if amount_usd is not None:
            source.amount_usd = amount_usd
        if remarks is not None:
            source.remarks = remarks
        await self.session.commit()
        await self.session.refresh(source)
        return source

    async def delete(self, student: User, source_id: UUID) -> None:
        source = await self._owned(student, source_id)
        if source.verification is FundingVerificationStatus.VERIFIED:
            raise BadRequestException("This funding source is verified and can no longer be removed.")
        await self.session.delete(source)
        await self.session.commit()


class LoanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for(self, student: User) -> StudentLoan:
        loan = await self.session.scalar(
            select(StudentLoan)
            .where(StudentLoan.student_id == student.id)
            .options(selectinload(StudentLoan.documents), selectinload(StudentLoan.disbursements))
        )
        if loan is None:
            raise NotFoundException("No loan on file")
        return loan


class BudgetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, student: User) -> StudentBudget:
        budget = await self.session.scalar(
            select(StudentBudget)
            .where(StudentBudget.student_id == student.id)
            .options(selectinload(StudentBudget.categories))
        )
        if budget is not None:
            return budget
        new_budget = StudentBudget(student_id=student.id)
        self.session.add(new_budget)
        await self.session.commit()
        # Re-fetched with `categories` eager-loaded rather than patched onto
        # the just-created row: assigning to a relationship collection still
        # lazy-loads the "old" value first to diff against, which fails
        # outside the async greenlet exactly like an unguarded read would.
        reloaded = await self.session.scalar(
            select(StudentBudget)
            .where(StudentBudget.id == new_budget.id)
            .options(selectinload(StudentBudget.categories))
        )
        assert reloaded is not None
        return reloaded

    async def replace(
        self, student: User, planned_monthly_income: Decimal | None, categories: list[dict]
    ) -> StudentBudget:
        """Replanning replaces the whole category list rather than diffing it
        — a budget is a plan the student rewrites wholesale, not a ledger
        whose old lines have their own history worth preserving."""
        budget = await self.get_or_create(student)
        budget.planned_monthly_income = planned_monthly_income
        for existing in list(budget.categories):
            await self.session.delete(existing)
        await self.session.flush()
        budget.categories = [
            BudgetCategory(
                budget_id=budget.id,
                category=row["category"],
                amount=row["amount"],
                essential=row.get("essential", True),
                order=order,
            )
            for order, row in enumerate(categories, start=1)
        ]
        await self.session.commit()
        await self.session.refresh(budget)
        return await self.get_or_create(student)


class SavingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for(self, student: User) -> list[StudentSavingsGoal]:
        result = await self.session.scalars(
            select(StudentSavingsGoal).where(StudentSavingsGoal.student_id == student.id)
        )
        return list(result)

    async def upsert(
        self,
        student: User,
        kind: SavingsGoalKind,
        *,
        current_savings: Decimal,
        monthly_contribution: Decimal | None = None,
        target_date: date | None = None,
        recommended_months: int | None = None,
    ) -> StudentSavingsGoal:
        goal = await self.session.scalar(
            select(StudentSavingsGoal).where(
                StudentSavingsGoal.student_id == student.id, StudentSavingsGoal.kind == kind
            )
        )
        if goal is None:
            goal = StudentSavingsGoal(student_id=student.id, kind=kind)
            self.session.add(goal)
        goal.current_savings = current_savings
        goal.monthly_contribution = monthly_contribution
        goal.target_date = target_date
        goal.recommended_months = recommended_months
        await self.session.commit()
        await self.session.refresh(goal)
        return goal


class FinanceCatalogService:
    """Read-only reference data: destination cost of living and currency
    rates. Seeded, never written by a route."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cost_of_living_for(self, country_id: UUID) -> CountryCostOfLiving:
        row = await self.session.scalar(
            select(CountryCostOfLiving)
            .where(CountryCostOfLiving.country_id == country_id)
            .options(selectinload(CountryCostOfLiving.cost_categories))
        )
        if row is None:
            raise NotFoundException("No cost-of-living data for this country")
        return row

    async def currency_rates(self) -> list[CurrencyRate]:
        result = await self.session.scalars(select(CurrencyRate).order_by(CurrencyRate.code))
        return list(result)


async def get_funding_source_service(session: AsyncSession = Depends(get_db_session)) -> FundingSourceService:
    return FundingSourceService(session)


async def get_loan_service(session: AsyncSession = Depends(get_db_session)) -> LoanService:
    return LoanService(session)


async def get_budget_service(session: AsyncSession = Depends(get_db_session)) -> BudgetService:
    return BudgetService(session)


async def get_savings_service(session: AsyncSession = Depends(get_db_session)) -> SavingsService:
    return SavingsService(session)


async def get_finance_catalog_service(session: AsyncSession = Depends(get_db_session)) -> FinanceCatalogService:
    return FinanceCatalogService(session)
