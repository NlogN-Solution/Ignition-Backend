"""Staff editing an application, and staff-set priority tasks.

Two regressions and one feature:

* `PATCH /applications/{id}` with dates 500'd — the six `String(10)` date
  columns were handed `date` objects and asyncpg refused them
  ("expected str, got date"). The staff "Edit application" dialog hit it on
  every save that included a date.
* `study_mode` and `intake_id` are staff-editable and read back.
* Priority tasks: staff set them, the student sees them on their checklist
  flagged `is_priority`, can complete them, and cannot delete or reword them.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

API = "/api/v1"


@pytest_asyncio.fixture
async def setup(client: AsyncClient, user_factory, auth_headers) -> dict:
    admin = await user_factory(UserRole.ADMIN, email="edit.admin@example.com")
    staff = await auth_headers(admin)
    country = (await client.post(f"{API}/countries", json={"name": "United Kingdom", "iso2": "GB"}, headers=staff)).json()
    university = (
        await client.post(f"{API}/universities", json={"country_id": country["id"], "name": "Arden University"}, headers=staff)
    ).json()
    program = (
        await client.post(f"{API}/programs", json={"university_id": university["id"], "name": "BSc Computing"}, headers=staff)
    ).json()
    student = await user_factory(UserRole.STUDENT, email="edit.student@example.com")
    application = (
        await client.post(
            f"{API}/applications", json={"student_id": str(student.id), "program_id": program["id"]}, headers=staff
        )
    ).json()
    return {
        "staff": staff,
        "program": program,
        "student": student,
        "student_headers": await auth_headers(student),
        "application": application,
    }


async def test_editing_string_backed_dates_saves_them(client: AsyncClient, setup: dict) -> None:
    response = await client.patch(
        f"{API}/applications/{setup['application']['id']}",
        json={
            "application_date": "2026-09-25",
            "submission_date": "2026-09-18",
            "offer_received_date": "2026-09-19",
            "visa_applied_date": "2026-10-01",
            "visa_decision_date": "2026-10-20",
            "enrollment_date": "2027-01-10",
            "application_deadline": "2026-09-08",
            "payment_deadline": "2026-09-15",
            "tuition_fee": 15500,
            "scholarship_amount": 2000,
            "university_application_id": "ARD-123",
        },
        headers=setup["staff"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert str(body["submission_date"])[:10] == "2026-09-18"
    assert str(body["enrollment_date"])[:10] == "2027-01-10"
    assert body["university_application_id"] == "ARD-123"


async def test_staff_set_study_mode_and_intake(client: AsyncClient, setup: dict) -> None:
    intake = (
        await client.post(
            f"{API}/intakes",
            json={"program_id": setup["program"]["id"], "name": "January 2027", "start_date": "2027-01-15"},
            headers=setup["staff"],
        )
    ).json()
    response = await client.patch(
        f"{API}/applications/{setup['application']['id']}",
        json={"study_mode": "Part-time", "intake_id": intake["id"]},
        headers=setup["staff"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["study_mode"] == "Part-time"
    assert response.json()["intake_id"] == intake["id"]


async def test_priority_task_round_trip(client: AsyncClient, setup: dict) -> None:
    student_id = setup["student"].id
    base = f"{API}/students/{student_id}/priority-tasks"

    created = await client.post(
        base, json={"title": "Upload your IELTS certificate", "due_date": "2026-10-01"}, headers=setup["staff"]
    )
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["is_priority"] is True
    assert task["assigned_by_name"]

    listed = (await client.get(base, headers=setup["staff"])).json()
    assert [item["id"] for item in listed] == [task["id"]]

    # The student sees it on their checklist, flagged…
    checklist = (await client.get(f"{API}/student/me/checklist", headers=setup["student_headers"])).json()
    mine = next(item for item in checklist["items"] if item["id"] == task["id"])
    assert mine["is_priority"] is True

    # …cannot delete or reword it…
    assert (
        await client.delete(f"{API}/student/me/checklist/{task['id']}", headers=setup["student_headers"])
    ).status_code == 400
    assert (
        await client.patch(
            f"{API}/student/me/checklist/{task['id']}", json={"title": "Something else"}, headers=setup["student_headers"]
        )
    ).status_code == 400

    # …but can complete it.
    done = await client.patch(
        f"{API}/student/me/checklist/{task['id']}", json={"completed": True}, headers=setup["student_headers"]
    )
    assert done.status_code == 200 and done.json()["is_complete"] is True

    # And staff can take it back.
    assert (await client.delete(f"{base}/{task['id']}", headers=setup["staff"])).status_code == 200
    assert (await client.get(base, headers=setup["staff"])).json() == []


async def test_students_cannot_set_priority_tasks(client: AsyncClient, setup: dict) -> None:
    response = await client.post(
        f"{API}/students/{setup['student'].id}/priority-tasks",
        json={"title": "Give myself a task"},
        headers=setup["student_headers"],
    )
    assert response.status_code == 403
