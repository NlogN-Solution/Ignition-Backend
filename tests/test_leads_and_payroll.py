"""Lead conversion and payroll computation.

The two remaining behaviours flagged as untested after the steps 6–10 port.
Payroll matters most: it turns attendance and leave records into money, and
nothing else checks that arithmetic.
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

LEADS = "/api/v1/leads"
PAYROLL_RUNS = "/api/v1/payroll-runs"
SALARY = "/api/v1/salary-structures"
USERS = "/api/v1/users"
ATTENDANCE = "/api/v1/attendance"


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, user_factory, auth_headers) -> dict[str, str]:
    admin = await user_factory(UserRole.ADMIN, email="hr.admin@example.com")
    return await auth_headers(admin)


def _lead_payload(**overrides) -> dict:
    payload = {"first_name": "Sita", "last_name": "Rai", "phone": "9800000000", "email": "sita@example.com"}
    payload.update(overrides)
    return payload


# ── Leads ─────────────────────────────────────────────────────────────────────


async def test_a_lead_moves_through_its_lifecycle(client: AsyncClient, admin_headers) -> None:
    created = await client.post(LEADS, json=_lead_payload(), headers=admin_headers)
    assert created.status_code == 200, created.text
    lead_id = created.json()["id"]

    qualified = await client.post(f"{LEADS}/{lead_id}/qualify", json={}, headers=admin_headers)
    assert qualified.status_code == 200, qualified.text
    assert qualified.json()["status"] == "qualified"

    activities = await client.get(f"{LEADS}/{lead_id}/activities", headers=admin_headers)
    assert activities.status_code == 200
    assert activities.json(), "lifecycle changes must leave an activity trail"


async def test_an_unknown_status_is_rejected_before_the_database(
    client: AsyncClient,
    admin_headers,
) -> None:
    """ED360 types `status` as a bare `str` and assigns it straight to the enum
    column, so a typo surfaces as a database error rather than a 422."""
    lead_id = (await client.post(LEADS, json=_lead_payload(), headers=admin_headers)).json()["id"]

    response = await client.post(
        f"{LEADS}/{lead_id}/status",
        json={"status": "definitely-not-a-status"},
        headers=admin_headers,
    )
    assert response.status_code == 422


async def test_converting_a_lead_creates_a_student(client: AsyncClient, admin_headers) -> None:
    lead_id = (await client.post(LEADS, json=_lead_payload(), headers=admin_headers)).json()["id"]

    converted = await client.post(f"{LEADS}/{lead_id}/convert", json={}, headers=admin_headers)
    assert converted.status_code == 200, converted.text
    body = converted.json()

    assert body["lead"]["status"] == "converted"
    assert body["student_user_id"] is not None
    assert body["created_new_user"] is True

    # The new account is a student, and is linked back to the lead it came from.
    student = await client.get(f"{USERS}/{body['student_user_id']}", headers=admin_headers)
    assert student.json()["role"] == "student"
    assert body["lead"]["converted_user_id"] == body["student_user_id"]


async def test_conversion_can_issue_portal_access(client: AsyncClient, admin_headers) -> None:
    lead_id = (await client.post(LEADS, json=_lead_payload(), headers=admin_headers)).json()["id"]

    converted = await client.post(
        f"{LEADS}/{lead_id}/convert",
        json={"create_portal_account": True},
        headers=admin_headers,
    )
    assert converted.status_code == 200, converted.text
    body = converted.json()
    assert body["portal_account_created"] is True
    # Returned exactly once, at creation.
    assert body["generated_password"]


async def test_students_cannot_touch_the_crm(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)
    assert (await client.get(LEADS, headers=headers)).status_code == 403
    assert (await client.post(LEADS, json=_lead_payload(), headers=headers)).status_code == 403


# ── Payroll ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def employee(client: AsyncClient, admin_headers, user_factory) -> dict:
    """A staff member with an employee profile and a salary structure."""
    staff = await user_factory(UserRole.COUNSELLOR, email="payroll.staff@example.com")

    profile = await client.patch(
        f"{USERS}/{staff.id}/employee-profile",
        json={"designation": "Counsellor", "joining_date": "2020-01-01"},
        headers=admin_headers,
    )
    assert profile.status_code == 200, profile.text

    salary = await client.put(
        f"{SALARY}/{staff.id}",
        json={"basic_salary": 60000.0, "currency": "NPR"},
        headers=admin_headers,
    )
    assert salary.status_code == 200, salary.text
    return {"user": staff}


async def test_a_salary_structure_requires_an_employee_profile(
    client: AsyncClient,
    admin_headers,
    user_factory,
) -> None:
    """Salary hangs off employment, so setting one for a bare account is a 400,
    not an orphaned row."""
    stranger = await user_factory(UserRole.SUPPORT, email="no.profile@example.com")
    response = await client.put(
        f"{SALARY}/{stranger.id}",
        json={"basic_salary": 1000.0},
        headers=admin_headers,
    )
    assert response.status_code == 400


async def test_a_payroll_run_generates_a_payslip_per_salaried_employee(
    client: AsyncClient,
    admin_headers,
    employee: dict,
) -> None:
    run = await client.post(
        PAYROLL_RUNS,
        json={"period_year": 2027, "period_month": 3},
        headers=admin_headers,
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["id"]
    assert run.json()["status"] == "draft"

    payslips = await client.get(f"{PAYROLL_RUNS}/{run_id}/payslips", headers=admin_headers)
    assert payslips.status_code == 200
    rows = payslips.json()["items"]
    assert len(rows) == 1

    payslip = rows[0]
    assert payslip["user_id"] == str(employee["user"].id)
    assert float(payslip["basic_salary"]) == 60000.0
    # Nobody checked in during a future month, so every working day is unpaid
    # and net pay must not be the full salary.
    assert payslip["unpaid_days"] > 0
    assert float(payslip["net_pay"]) < 60000.0


async def test_employees_without_a_salary_structure_are_skipped_not_guessed(
    client: AsyncClient,
    admin_headers,
    employee: dict,
    user_factory,
) -> None:
    unsalaried = await user_factory(UserRole.SUPPORT, email="unsalaried@example.com")
    await client.patch(
        f"{USERS}/{unsalaried.id}/employee-profile",
        json={"designation": "Support"},
        headers=admin_headers,
    )

    run = await client.post(PAYROLL_RUNS, json={"period_year": 2027, "period_month": 4}, headers=admin_headers)
    assert run.status_code == 200, run.text

    payslips = (await client.get(f"{PAYROLL_RUNS}/{run.json()['id']}/payslips", headers=admin_headers)).json()["items"]
    assert [p["user_id"] for p in payslips] == [str(employee["user"].id)]


async def test_a_period_cannot_be_run_twice(client: AsyncClient, admin_headers, employee: dict) -> None:
    first = await client.post(PAYROLL_RUNS, json={"period_year": 2027, "period_month": 5}, headers=admin_headers)
    assert first.status_code == 200

    duplicate = await client.post(PAYROLL_RUNS, json={"period_year": 2027, "period_month": 5}, headers=admin_headers)
    assert duplicate.status_code in {400, 409}, "a second run for one period would double-pay"


async def test_line_items_move_the_net_and_survive_removal(
    client: AsyncClient,
    admin_headers,
    employee: dict,
) -> None:
    run = await client.post(PAYROLL_RUNS, json={"period_year": 2027, "period_month": 6}, headers=admin_headers)
    payslip = (await client.get(f"{PAYROLL_RUNS}/{run.json()['id']}/payslips", headers=admin_headers)).json()["items"][
        0
    ]
    baseline = float(payslip["net_pay"])

    added = await client.post(
        f"/api/v1/payslips/{payslip['id']}/line-items",
        json={"type": "addition", "label": "Festival bonus", "amount": 5000.0},
        headers=admin_headers,
    )
    assert added.status_code == 200, added.text
    assert float(added.json()["net_pay"]) == pytest.approx(baseline + 5000.0)

    item_id = added.json()["line_items"][-1]["id"]
    removed = await client.delete(
        f"/api/v1/payslips/{payslip['id']}/line-items/{item_id}",
        headers=admin_headers,
    )
    assert removed.status_code == 200
    assert float(removed.json()["net_pay"]) == pytest.approx(baseline)


async def test_finalizing_then_marking_paid_moves_the_run_forward(
    client: AsyncClient,
    admin_headers,
    employee: dict,
) -> None:
    run_id = (
        await client.post(PAYROLL_RUNS, json={"period_year": 2027, "period_month": 7}, headers=admin_headers)
    ).json()["id"]

    finalized = await client.post(f"{PAYROLL_RUNS}/{run_id}/finalize", headers=admin_headers)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "finalized"

    paid = await client.post(f"{PAYROLL_RUNS}/{run_id}/mark-paid", headers=admin_headers)
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"


async def test_an_employee_reads_their_own_payslips_only(
    client: AsyncClient,
    admin_headers,
    employee: dict,
    user_factory,
    auth_headers,
) -> None:
    await client.post(PAYROLL_RUNS, json={"period_year": 2027, "period_month": 8}, headers=admin_headers)

    owner_headers = await auth_headers(employee["user"])
    own = await client.get(f"{USERS}/{employee['user'].id}/payslips", headers=owner_headers)
    assert own.status_code == 200
    assert len(own.json()["items"]) >= 1

    colleague = await user_factory(UserRole.SUPPORT, email="nosy@example.com")
    snooped = await client.get(
        f"{USERS}/{employee['user'].id}/payslips",
        headers=await auth_headers(colleague),
    )
    assert snooped.status_code == 403, "salary is not readable across colleagues"


async def test_students_cannot_reach_payroll(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    assert (await client.get(PAYROLL_RUNS, headers=await auth_headers(student))).status_code == 403


# ── Attendance policy ─────────────────────────────────────────────────────────


async def test_the_attendance_policy_is_a_singleton(client: AsyncClient, admin_headers) -> None:
    """ED360 keeps one policy per organization; here the database enforces one
    row, full stop."""
    first = await client.patch(
        f"{ATTENDANCE}/policy",
        json={"grace_period_minutes": 15},
        headers=admin_headers,
    )
    assert first.status_code == 200, first.text
    policy_id = first.json()["id"]

    second = await client.patch(
        f"{ATTENDANCE}/policy",
        json={"grace_period_minutes": 20},
        headers=admin_headers,
    )
    assert second.status_code == 200
    assert second.json()["id"] == policy_id, "a second update must edit the one policy, not add another"
    assert second.json()["grace_period_minutes"] == 20


async def test_checking_in_twice_is_a_conflict(client: AsyncClient, user_factory, auth_headers) -> None:
    staff = await user_factory(UserRole.COUNSELLOR, email="checkin@example.com")
    headers = await auth_headers(staff)

    first = await client.post(f"{ATTENDANCE}/check-in", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["date"] == date.today().isoformat()

    second = await client.post(f"{ATTENDANCE}/check-in", headers=headers)
    assert second.status_code == 409
