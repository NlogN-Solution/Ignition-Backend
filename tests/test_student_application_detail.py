"""The student portal's application page and Documents page.

The application read carries the university/course particulars and the
staff-set deadlines and notice; the documents list says which applications
each file is filed against; and a student may replace or delete what they
uploaded themselves — never what staff filed, and never an approved file.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"
APPLICATIONS = "/api/v1/applications"


@pytest_asyncio.fixture
async def setup(client: AsyncClient, user_factory, auth_headers) -> dict:
    admin = await user_factory(UserRole.ADMIN, email="ad.admin@example.com")
    admin_headers = await auth_headers(admin)
    counsellor = await user_factory(UserRole.COUNSELLOR, email="ad.counsellor@example.com")
    staff_headers = await auth_headers(counsellor)

    country = await client.post("/api/v1/countries", json={"name": "Scotland", "iso2": "SC"}, headers=admin_headers)
    university = await client.post(
        "/api/v1/universities",
        json={"country_id": country.json()["id"], "name": "University of Aberdeen", "city": "Aberdeen"},
        headers=admin_headers,
    )
    program = await client.post(
        "/api/v1/programs",
        json={
            "university_id": university.json()["id"],
            "name": "BSc (Hons) Computing (Top-Up)",
            "tuition_fee": 23800,
            "currency": "GBP",
            "duration_months": 12,
        },
        headers=admin_headers,
    )

    student = await user_factory(UserRole.STUDENT, email="ad.student@example.com")
    headers = await auth_headers(student)
    application = await client.post(
        APPLICATIONS,
        json={
            "student_id": str(student.id),
            "program_id": program.json()["id"],
            "counsellor_id": str(counsellor.id),
        },
        headers=staff_headers,
    )
    assert application.status_code == 200, application.text
    return {
        "student": student,
        "headers": headers,
        "staff_headers": staff_headers,
        "counsellor": counsellor,
        "application": application.json(),
    }


async def _upload(client: AsyncClient, setup: dict, *, application_id: str | None = None) -> dict:
    data = {"student_id": str(setup["student"].id), "document_type": "passport"}
    if application_id:
        data["application_id"] = application_id
    response = await client.post(
        "/api/v1/documents/upload",
        data=data,
        files={"file": ("passport.pdf", b"%PDF-1.4 scan", "application/pdf")},
        headers=setup["headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_staff_set_deadlines_and_notice_reach_the_student(client: AsyncClient, setup) -> None:
    application_id = setup["application"]["id"]
    patched = await client.patch(
        f"{APPLICATIONS}/{application_id}",
        json={
            "application_deadline": "2026-08-02",
            "payment_deadline": "2026-08-23",
            "condition_deadline": "2026-08-16",
            "student_notice": "Bring your original transcript.",
        },
        headers=setup["staff_headers"],
    )
    assert patched.status_code == 200, patched.text

    response = await client.get(f"{STUDENT}/me/applications/{application_id}", headers=setup["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["application_deadline"] == "2026-08-02"
    assert body["payment_deadline"] == "2026-08-23"
    assert body["condition_deadline"] == "2026-08-16"
    assert body["student_notice"] == "Bring your original transcript."
    # The advisor comes with a way to reach them.
    assert body["counsellor"]["email"] == setup["counsellor"].email
    # The detail read carries the particulars the page renders.
    assert body["program"]["university"]["city"] == "Aberdeen"
    assert body["program"]["course"]["tuition_fee"] == 23800
    assert body["program"]["course"]["currency"] == "GBP"


async def test_the_list_stays_light(client: AsyncClient, setup) -> None:
    response = await client.get(f"{STUDENT}/me/applications", headers=setup["headers"])
    assert response.status_code == 200
    program = response.json()["items"][0]["program"]
    assert program["university_name"] == "University of Aberdeen"
    assert program["university"] is None
    assert program["course"] is None


async def test_documents_say_which_applications_use_them(client: AsyncClient, setup) -> None:
    await _upload(client, setup, application_id=setup["application"]["id"])
    response = await client.get(f"{STUDENT}/me/documents", headers=setup["headers"])
    assert response.status_code == 200, response.text
    [document] = response.json()["items"]
    assert [a["university_name"] for a in document["applications"]] == ["University of Aberdeen"]


async def test_a_student_replaces_their_own_file_and_it_goes_back_to_review(client: AsyncClient, setup) -> None:
    document = await _upload(client, setup, application_id=setup["application"]["id"])
    verified = await client.post(
        f"/api/v1/documents/{document['id']}/verify", json={}, headers=setup["staff_headers"]
    )
    assert verified.json()["status"] == "approved"

    response = await client.post(
        f"{STUDENT}/me/documents/{document['id']}/replace",
        files={"file": ("passport-v2.pdf", b"%PDF-1.4 new scan", "application/pdf")},
        headers=setup["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == document["id"]
    assert body["original_file_name"] == "passport-v2.pdf"
    assert body["status"] == "pending"
    assert body["verified_at"] is None
    # Still filed against the same application.
    assert len(body["applications"]) == 1


async def test_approved_documents_cannot_be_deleted(client: AsyncClient, setup) -> None:
    document = await _upload(client, setup)
    await client.post(f"/api/v1/documents/{document['id']}/verify", json={}, headers=setup["staff_headers"])
    response = await client.delete(f"{STUDENT}/me/documents/{document['id']}", headers=setup["headers"])
    assert response.status_code == 409


async def test_a_student_deletes_a_pending_upload(client: AsyncClient, setup) -> None:
    document = await _upload(client, setup)
    response = await client.delete(f"{STUDENT}/me/documents/{document['id']}", headers=setup["headers"])
    assert response.status_code == 204
    listing = await client.get(f"{STUDENT}/me/documents", headers=setup["headers"])
    assert listing.json()["items"] == []


async def test_staff_filed_documents_are_not_the_students_to_change(client: AsyncClient, setup) -> None:
    filed = await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(setup["student"].id), "document_type": "passport"},
        files={"file": ("filed.pdf", b"%PDF-1.4", "application/pdf")},
        headers=setup["staff_headers"],
    )
    assert filed.status_code == 200, filed.text
    document_id = filed.json()["id"]

    delete = await client.delete(f"{STUDENT}/me/documents/{document_id}", headers=setup["headers"])
    replace = await client.post(
        f"{STUDENT}/me/documents/{document_id}/replace",
        files={"file": ("mine.pdf", b"%PDF-1.4", "application/pdf")},
        headers=setup["headers"],
    )
    assert delete.status_code == 403
    assert replace.status_code == 403


async def test_another_students_document_is_a_404(client: AsyncClient, setup, user_factory, auth_headers) -> None:
    document = await _upload(client, setup)
    other = await user_factory(UserRole.STUDENT, email="ad.other@example.com")
    response = await client.delete(f"{STUDENT}/me/documents/{document['id']}", headers=await auth_headers(other))
    assert response.status_code == 404
