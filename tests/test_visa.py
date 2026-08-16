"""Phase 6 — the visa and pre-departure journey.

There is no route to create a `VisaCase` — staff open one when an application
reaches the visa stage — so these tests build the case directly at the ORM
layer, the way `test_progress_points.py` builds its catalog rows, and then
drive the student's read/limited-write surface over HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models import (
    DepartureChecklistItem,
    VisaAppointment,
    VisaCase,
    VisaDocumentRequirement,
    VisaFee,
    VisaStage,
)
from app.models.enums import UserRole, VisaAppointmentStatus, VisaStageState

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"


@pytest_asyncio.fixture
async def visa_case(client: AsyncClient, session, user_factory, auth_headers) -> dict:
    admin = await user_factory(UserRole.ADMIN, email="visa.admin@example.com")
    admin_headers = await auth_headers(admin)
    country = await client.post("/api/v1/countries", json={"name": "Canada", "iso2": "CA"}, headers=admin_headers)
    university = await client.post(
        "/api/v1/universities",
        json={"country_id": country.json()["id"], "name": "University of Toronto"},
        headers=admin_headers,
    )
    program = await client.post(
        "/api/v1/programs",
        json={"university_id": university.json()["id"], "name": "MEng Robotics"},
        headers=admin_headers,
    )

    student = await user_factory(UserRole.STUDENT, email="visa.student@example.com")
    student_headers = await auth_headers(student)
    application = await client.post(
        "/api/v1/applications",
        json={"student_id": str(student.id), "program_id": program.json()["id"]},
        headers=admin_headers,
    )

    # Built directly at the ORM layer, on the same session the test's HTTP
    # client shares — there is no route that creates a `VisaCase`.
    case = VisaCase(
        application_id=application.json()["id"],
        student_id=student.id,
        destination_country="Canada",
        visa_type="Study Permit",
    )
    session.add(case)
    await session.flush()

    stage = VisaStage(case_id=case.id, label="Offer accepted", order=1, state=VisaStageState.CURRENT)
    appointment = VisaAppointment(case_id=case.id, title="Medical examination", provider="Panel physician")
    document = VisaDocumentRequirement(case_id=case.id, title="Passport", order=1)
    fee = VisaFee(case_id=case.id, label="Study permit fee", amount_usd="175.00")
    departure_item = DepartureChecklistItem(case_id=case.id, title="Book flights", order=1)
    session.add_all([stage, appointment, document, fee, departure_item])
    await session.commit()

    return {
        "student": student,
        "headers": student_headers,
        "case_id": case.id,
        "appointment_id": appointment.id,
        "fee_id": fee.id,
        "departure_item_id": departure_item.id,
    }


async def test_get_my_visa_case_returns_the_full_shape(client: AsyncClient, visa_case) -> None:
    response = await client.get(f"{STUDENT}/me/visa", headers=visa_case["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["destination_country"] == "Canada"
    assert len(body["stages"]) == 1
    assert len(body["appointments"]) == 1
    assert len(body["required_documents"]) == 1
    assert len(body["fees"]) == 1
    assert len(body["departure_checklist"]) == 1


async def test_a_student_with_no_case_gets_404(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="visa.none@example.com")
    response = await client.get(f"{STUDENT}/me/visa", headers=await auth_headers(student))
    assert response.status_code == 404


async def test_booking_an_appointment_sets_status_and_time(client: AsyncClient, visa_case) -> None:
    when = datetime(2026, 11, 5, 9, 0, tzinfo=UTC).isoformat()
    response = await client.post(
        f"{STUDENT}/me/visa/appointments/{visa_case['appointment_id']}/book",
        json={"scheduled_at": when},
        headers=visa_case["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == VisaAppointmentStatus.BOOKED.value
    assert body["scheduled_at"] is not None


async def test_cancelling_an_appointment(client: AsyncClient, visa_case) -> None:
    headers = visa_case["headers"]
    await client.post(
        f"{STUDENT}/me/visa/appointments/{visa_case['appointment_id']}/book",
        json={"scheduled_at": datetime(2026, 11, 5, 9, 0, tzinfo=UTC).isoformat()},
        headers=headers,
    )
    response = await client.post(
        f"{STUDENT}/me/visa/appointments/{visa_case['appointment_id']}/cancel", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == VisaAppointmentStatus.CANCELLED.value


async def test_marking_a_fee_paid_and_unpaid(client: AsyncClient, visa_case) -> None:
    headers = visa_case["headers"]
    paid = await client.patch(f"{STUDENT}/me/visa/fees/{visa_case['fee_id']}", json={"paid": True}, headers=headers)
    assert paid.status_code == 200, paid.text
    assert paid.json()["paid"] is True
    assert paid.json()["paid_at"] is not None

    unpaid = await client.patch(f"{STUDENT}/me/visa/fees/{visa_case['fee_id']}", json={"paid": False}, headers=headers)
    assert unpaid.json()["paid"] is False
    assert unpaid.json()["paid_at"] is None


async def test_ticking_a_departure_checklist_item(client: AsyncClient, visa_case) -> None:
    headers = visa_case["headers"]
    response = await client.patch(
        f"{STUDENT}/me/visa/departure-checklist/{visa_case['departure_item_id']}",
        json={"completed": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_complete"] is True

    case = (await client.get(f"{STUDENT}/me/visa", headers=headers)).json()
    assert case["departure_checklist"][0]["is_complete"] is True


async def test_a_student_cannot_touch_another_students_visa_case(
    client: AsyncClient, visa_case, user_factory, auth_headers
) -> None:
    other = await user_factory(UserRole.STUDENT, email="visa.other@example.com")
    other_headers = await auth_headers(other)

    response = await client.get(f"{STUDENT}/me/visa", headers=other_headers)
    assert response.status_code == 404

    book = await client.post(
        f"{STUDENT}/me/visa/appointments/{visa_case['appointment_id']}/book",
        json={"scheduled_at": datetime(2026, 11, 5, 9, 0, tzinfo=UTC).isoformat()},
        headers=other_headers,
    )
    assert book.status_code == 404
