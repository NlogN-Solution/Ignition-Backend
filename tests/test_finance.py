"""Phase 6 — the student's own funding picture.

Most of this module is student-writable — a funding source, a budget, a
savings goal are the student's own plan, not facts the platform observed.
`StudentLoan` is the one exception (read-only), and the cost-of-living /
currency-rate catalogs are seeded reference data rather than per-student rows,
so their fixtures are built directly at the ORM layer like `test_visa.py`'s.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models import (
    Country,
    CountryCostOfLiving,
    CurrencyRate,
    LoanDocument,
    StudentLoan,
)
from app.models.enums import LoanProcessingStatus, UserRole

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"


@pytest_asyncio.fixture
async def student(client: AsyncClient, user_factory, auth_headers) -> dict:
    user = await user_factory(UserRole.STUDENT, email="finance.student@example.com")
    return {"user": user, "headers": await auth_headers(user)}


# ── Funding sources ─────────────────────────────────────────────────────────


async def test_a_student_can_add_a_funding_source(client: AsyncClient, student) -> None:
    response = await client.post(
        f"{STUDENT}/me/finance/funding-sources",
        json={"source_type": "Personal Savings", "provider": "Self-funded", "amount_usd": "9000.00"},
        headers=student["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verification"] == "unverified"
    assert body["amount_usd"] == "9000.00"


async def test_editing_an_unverified_funding_source(client: AsyncClient, student) -> None:
    headers = student["headers"]
    created = await client.post(
        f"{STUDENT}/me/finance/funding-sources",
        json={"source_type": "Parents", "amount_usd": "8000.00"},
        headers=headers,
    )
    source_id = created.json()["id"]

    response = await client.patch(
        f"{STUDENT}/me/finance/funding-sources/{source_id}", json={"amount_usd": "8500.00"}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["amount_usd"] == "8500.00"


async def test_a_verified_funding_source_cannot_be_edited_or_deleted(client: AsyncClient, student, session) -> None:
    """Once staff verify a funding source it is on record for the visa
    application — the student rewriting it under staff's feet would silently
    invalidate what was already relied on."""
    headers = student["headers"]
    created = await client.post(
        f"{STUDENT}/me/finance/funding-sources",
        json={"source_type": "Employer", "amount_usd": "2500.00"},
        headers=headers,
    )
    source_id = created.json()["id"]

    from app.models import StudentFundingSource
    from app.models.enums import FundingVerificationStatus

    row = await session.get(StudentFundingSource, source_id)
    row.verification = FundingVerificationStatus.VERIFIED
    await session.commit()

    edit = await client.patch(
        f"{STUDENT}/me/finance/funding-sources/{source_id}", json={"amount_usd": "9999.00"}, headers=headers
    )
    assert edit.status_code == 400

    delete = await client.delete(f"{STUDENT}/me/finance/funding-sources/{source_id}", headers=headers)
    assert delete.status_code == 400


async def test_deleting_an_unverified_funding_source(client: AsyncClient, student) -> None:
    headers = student["headers"]
    created = await client.post(
        f"{STUDENT}/me/finance/funding-sources",
        json={"source_type": "Relative Sponsor", "amount_usd": "6000.00"},
        headers=headers,
    )
    source_id = created.json()["id"]

    response = await client.delete(f"{STUDENT}/me/finance/funding-sources/{source_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["success"] is True

    remaining = await client.get(f"{STUDENT}/me/finance/funding-sources", headers=headers)
    assert remaining.json() == []


async def test_a_student_cannot_touch_another_students_funding_source(
    client: AsyncClient, student, user_factory, auth_headers
) -> None:
    created = await client.post(
        f"{STUDENT}/me/finance/funding-sources",
        json={"source_type": "Personal Savings", "amount_usd": "1000.00"},
        headers=student["headers"],
    )
    source_id = created.json()["id"]

    other = await user_factory(UserRole.STUDENT, email="finance.other@example.com")
    other_headers = await auth_headers(other)
    response = await client.patch(
        f"{STUDENT}/me/finance/funding-sources/{source_id}", json={"amount_usd": "1.00"}, headers=other_headers
    )
    assert response.status_code == 404


# ── Loan (read-only) ────────────────────────────────────────────────────────


async def test_a_student_with_no_loan_gets_404(client: AsyncClient, student) -> None:
    response = await client.get(f"{STUDENT}/me/finance/loan", headers=student["headers"])
    assert response.status_code == 404


async def test_getting_my_loan_with_its_documents(client: AsyncClient, student, session) -> None:
    loan = StudentLoan(
        student_id=student["user"].id,
        provider="International Student Bank",
        approved_amount="30000.00",
        disbursed_amount="12000.00",
        processing_status=LoanProcessingStatus.PARTIALLY_DISBURSED,
    )
    session.add(loan)
    await session.flush()
    session.add(LoanDocument(loan_id=loan.id, name="Sanction letter", status="verified"))
    await session.commit()

    response = await client.get(f"{STUDENT}/me/finance/loan", headers=student["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processing_status"] == "partially_disbursed"
    assert len(body["documents"]) == 1


# ── Budget ──────────────────────────────────────────────────────────────────


async def test_budget_starts_empty(client: AsyncClient, student) -> None:
    response = await client.get(f"{STUDENT}/me/finance/budget", headers=student["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["planned_monthly_income"] is None
    assert body["categories"] == []


async def test_replacing_the_budget_replaces_categories_wholesale(client: AsyncClient, student) -> None:
    headers = student["headers"]
    first = await client.put(
        f"{STUDENT}/me/finance/budget",
        json={
            "planned_monthly_income": "1450.00",
            "categories": [
                {"category": "Accommodation", "amount": "720.00", "essential": True},
                {"category": "Food", "amount": "300.00", "essential": True},
            ],
        },
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["categories"]) == 2

    second = await client.put(
        f"{STUDENT}/me/finance/budget",
        json={"planned_monthly_income": "1500.00", "categories": [{"category": "Rent", "amount": "800.00"}]},
        headers=headers,
    )
    assert second.status_code == 200
    assert len(second.json()["categories"]) == 1
    assert second.json()["categories"][0]["category"] == "Rent"


# ── Savings and emergency fund ──────────────────────────────────────────────


async def test_savings_and_emergency_fund_are_independent_kinds(client: AsyncClient, student) -> None:
    headers = student["headers"]
    await client.put(
        f"{STUDENT}/me/finance/savings/savings",
        json={"current_savings": "9000.00", "monthly_contribution": "650.00", "target_date": "2026-12-01"},
        headers=headers,
    )
    await client.put(
        f"{STUDENT}/me/finance/savings/emergency_fund",
        json={"current_savings": "1800.00", "recommended_months": 3},
        headers=headers,
    )

    body = (await client.get(f"{STUDENT}/me/finance/savings", headers=headers)).json()
    by_kind = {g["kind"]: g for g in body}
    assert by_kind["savings"]["current_savings"] == "9000.00"
    assert by_kind["emergency_fund"]["recommended_months"] == 3


async def test_upserting_a_savings_goal_updates_the_same_row(client: AsyncClient, student) -> None:
    headers = student["headers"]
    await client.put(f"{STUDENT}/me/finance/savings/savings", json={"current_savings": "1000.00"}, headers=headers)
    await client.put(f"{STUDENT}/me/finance/savings/savings", json={"current_savings": "1200.00"}, headers=headers)

    body = (await client.get(f"{STUDENT}/me/finance/savings", headers=headers)).json()
    assert len(body) == 1
    assert body[0]["current_savings"] == "1200.00"


# ── Catalog: cost of living and currency rates ──────────────────────────────


@pytest_asyncio.fixture
async def canada(session) -> Country:
    country = Country(name="Canada", iso2="CA")
    session.add(country)
    await session.flush()
    cost = CountryCostOfLiving(
        country_id=country.id,
        proof_of_funds_name="GIC",
        tuition_usd="42000.00",
        living_cost_usd="15000.00",
    )
    session.add(cost)
    session.add(CurrencyRate(code="CAD", name="Canadian Dollar", symbol="C$", per_usd="1.36", change_pct="-0.4"))
    await session.commit()
    return country


async def test_cost_of_living_for_a_country(client: AsyncClient, student, canada) -> None:
    response = await client.get(f"{STUDENT}/catalog/countries/{canada.id}/cost-of-living", headers=student["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["tuition_usd"] == "42000.00"


async def test_cost_of_living_404s_for_a_country_with_no_data(client: AsyncClient, student) -> None:
    from uuid import uuid4

    response = await client.get(f"{STUDENT}/catalog/countries/{uuid4()}/cost-of-living", headers=student["headers"])
    assert response.status_code == 404


async def test_currency_rates_list(client: AsyncClient, student, canada) -> None:
    response = await client.get(f"{STUDENT}/catalog/currency-rates", headers=student["headers"])
    assert response.status_code == 200, response.text
    codes = [r["code"] for r in response.json()]
    assert "CAD" in codes
