"""The student's own funding picture, plus the catalog it's measured against.

Phase 6, seventh module. Everything under `student_id` here is the student's
own plan and is at least partly self-reported — a funding source, a budget, a
savings goal are claims about their finances, not facts the platform observed.
`StudentLoan` is the exception: staff or the lender maintain it, so the
student's surface over it is read-only, the same posture as `VisaCase`.

Real money that actually moves through the platform — tuition deposits, the
application fee — is **not** modelled here. That already exists as `Payment`
(`/student/me/payments`, Phase 5a); this module is what a student plans and
tracks on their own, not a second payments ledger.

`CountryCostOfLiving` and `CurrencyRate` are catalogs, not per-student data —
sourced from the portal's `countryFinance.json` / `currencyRates.json` and
seeded once, the same way `ProgressMilestone` or `InterviewType` are.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import (
    FundingSourceStatus,
    FundingVerificationStatus,
    LoanDisbursementStatus,
    LoanDocumentStatus,
    LoanProcessingStatus,
    SavingsGoalKind,
)

if TYPE_CHECKING:
    from .academic import Country
    from .document import Document
    from .user import User


class StudentFundingSource(Base, UUIDPKMixin, TimestampMixin):
    """One line of "how am I paying for this" — a loan, savings, a sponsor.

    Self-reported and student-editable up to the point staff verify it: once
    `verification` is `VERIFIED` it is on record for the visa application, and
    the service refuses further edits the same way a verified document is not
    silently rewritten under staff's feet.
    """

    __tablename__ = "student_funding_sources"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(200))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[FundingSourceStatus] = mapped_column(
        enum_type(FundingSourceStatus, "funding_source_status", create_type=False),
        nullable=False,
        server_default=FundingSourceStatus.PENDING.value,
    )
    verification: Mapped[FundingVerificationStatus] = mapped_column(
        enum_type(FundingVerificationStatus, "funding_verification_status", create_type=False),
        nullable=False,
        server_default=FundingVerificationStatus.UNVERIFIED.value,
    )
    proof_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    remarks: Mapped[str | None] = mapped_column(Text)

    student: Mapped[User] = relationship()
    proof_document: Mapped[Document | None] = relationship()

    __table_args__ = (Index("idx_student_funding_sources_student_id", "student_id"),)

    def __repr__(self) -> str:
        return f"<StudentFundingSource student_id={self.student_id} source_type={self.source_type!r}>"


class StudentLoan(Base, UUIDPKMixin, TimestampMixin):
    """The one education loan a student is tracking. Read-only to the
    student — the lender's numbers are not theirs to edit."""

    __tablename__ = "student_loans"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(200), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(200))
    approved_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    disbursed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    processing_status: Mapped[LoanProcessingStatus] = mapped_column(
        enum_type(LoanProcessingStatus, "loan_processing_status", create_type=False),
        nullable=False,
        server_default=LoanProcessingStatus.PENDING.value,
    )
    sanctioned_at: Mapped[date | None] = mapped_column(Date)
    expected_full_disbursement: Mapped[date | None] = mapped_column(Date)
    repayment_starts_at: Mapped[date | None] = mapped_column(Date)
    tenure_months: Mapped[int | None] = mapped_column(Integer)
    monthly_repayment: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    collateral: Mapped[str | None] = mapped_column(Text)

    student: Mapped[User] = relationship()
    documents: Mapped[list[LoanDocument]] = relationship(
        back_populates="loan", cascade="all, delete-orphan", order_by="LoanDocument.created_at"
    )
    disbursements: Mapped[list[LoanDisbursement]] = relationship(
        back_populates="loan", cascade="all, delete-orphan", order_by="LoanDisbursement.disbursement_date"
    )

    def __repr__(self) -> str:
        return f"<StudentLoan student_id={self.student_id} provider={self.provider!r}>"


class LoanDocument(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "loan_documents"

    loan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_loans.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[LoanDocumentStatus] = mapped_column(
        enum_type(LoanDocumentStatus, "loan_document_status", create_type=False),
        nullable=False,
        server_default=LoanDocumentStatus.PENDING.value,
    )

    loan: Mapped[StudentLoan] = relationship(back_populates="documents")

    __table_args__ = (Index("idx_loan_documents_loan_id", "loan_id"),)

    def __repr__(self) -> str:
        return f"<LoanDocument loan_id={self.loan_id} name={self.name!r}>"


class LoanDisbursement(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "loan_disbursements"

    loan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_loans.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    disbursement_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[LoanDisbursementStatus] = mapped_column(
        enum_type(LoanDisbursementStatus, "loan_disbursement_status", create_type=False),
        nullable=False,
        server_default=LoanDisbursementStatus.SCHEDULED.value,
    )

    loan: Mapped[StudentLoan] = relationship(back_populates="disbursements")

    __table_args__ = (Index("idx_loan_disbursements_loan_id", "loan_id"),)

    def __repr__(self) -> str:
        return f"<LoanDisbursement loan_id={self.loan_id} label={self.label!r}>"


class StudentBudget(Base, UUIDPKMixin, TimestampMixin):
    """The student's own planned monthly spend once they arrive. One per
    student — replanning replaces the categories, it does not add a second
    budget."""

    __tablename__ = "student_budgets"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    planned_monthly_income: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    student: Mapped[User] = relationship()
    categories: Mapped[list[BudgetCategory]] = relationship(
        back_populates="budget", cascade="all, delete-orphan", order_by="BudgetCategory.order"
    )

    def __repr__(self) -> str:
        return f"<StudentBudget student_id={self.student_id}>"


class BudgetCategory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "budget_categories"

    budget_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_budgets.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    essential: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    budget: Mapped[StudentBudget] = relationship(back_populates="categories")

    __table_args__ = (Index("idx_budget_categories_budget_id", "budget_id"),)

    def __repr__(self) -> str:
        return f"<BudgetCategory budget_id={self.budget_id} category={self.category!r}>"


class StudentSavingsGoal(Base, UUIDPKMixin, TimestampMixin):
    """Either the student's savings goal or their emergency fund — two rows of
    the same shape, distinguished by `kind` rather than two tables, since both
    are "an amount I'm building toward" and the portal renders them as
    siblings on the same screen."""

    __tablename__ = "student_savings_goals"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[SavingsGoalKind] = mapped_column(
        enum_type(SavingsGoalKind, "savings_goal_kind", create_type=False), nullable=False
    )
    current_savings: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    #: Meaningful for `SAVINGS`, not for `EMERGENCY_FUND`.
    monthly_contribution: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    target_date: Mapped[date | None] = mapped_column(Date)
    #: Meaningful for `EMERGENCY_FUND`, not for `SAVINGS`.
    recommended_months: Mapped[int | None] = mapped_column(Integer)

    student: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("student_id", "kind", name="uq_student_savings_goals_student_id_kind"),
        Index("idx_student_savings_goals_student_id", "student_id"),
    )

    def __repr__(self) -> str:
        return f"<StudentSavingsGoal student_id={self.student_id} kind={self.kind}>"


class CountryCostOfLiving(Base, UUIDPKMixin, TimestampMixin):
    """The catalog a student's own budget is measured against. One row per
    destination `Country`, sourced from `countryFinance.json` and seeded like
    any other catalog — not per-student data."""

    __tablename__ = "country_cost_of_living"

    country_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    proof_of_funds_name: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    tuition_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    living_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    insurance_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    visa_fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    biometrics_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    country: Mapped[Country] = relationship()
    cost_categories: Mapped[list[CostOfLivingCategory]] = relationship(
        back_populates="cost_of_living", cascade="all, delete-orphan", order_by="CostOfLivingCategory.order"
    )

    def __repr__(self) -> str:
        return f"<CountryCostOfLiving country_id={self.country_id}>"


class CostOfLivingCategory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "cost_of_living_categories"

    cost_of_living_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("country_cost_of_living.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    essential: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    #: Free text matching the portal's phase filter — "pre-arrival",
    #: "arrival", "ongoing" — not an enum, so a new phase needs no migration.
    phase: Mapped[str | None] = mapped_column(String(30))
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    cost_of_living: Mapped[CountryCostOfLiving] = relationship(back_populates="cost_categories")

    __table_args__ = (Index("idx_cost_of_living_categories_cost_of_living_id", "cost_of_living_id"),)

    def __repr__(self) -> str:
        return f"<CostOfLivingCategory cost_of_living_id={self.cost_of_living_id} category={self.category!r}>"


class CurrencyRate(Base, UUIDPKMixin, TimestampMixin):
    """A snapshot exchange rate against USD.

    Static, refreshed by re-seeding — the docstring in `currencyRates.json`
    says as much: "swap for a live rates endpoint later." `trend` keeps the
    portal's small history sparkline; it is JSONB rather than a table of its
    own because nothing ever queries into it, only renders it whole.
    """

    __tablename__ = "currency_rates"

    code: Mapped[str] = mapped_column(String(3), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(10))
    per_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    trend: Mapped[list[float] | None] = mapped_column(JSONB)
    updated_at_source: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    def __repr__(self) -> str:
        return f"<CurrencyRate code={self.code} per_usd={self.per_usd}>"
