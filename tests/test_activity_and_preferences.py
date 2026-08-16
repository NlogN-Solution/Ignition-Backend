"""Phase 6 — the student's activity feed and dashboard preferences.

Activity has no new tables — it's a projection over `ActivityLog` (document
uploads) and `ApplicationStatusHistory` (application transitions), each
scoped to the student through the entity it names, since neither table has
its own `student_id`. Preferences is one settings row, materialised lazily
like `StudentBudget`.
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
async def student_with_application(client: AsyncClient, user_factory, auth_headers) -> dict:
    admin = await user_factory(UserRole.ADMIN, email="activity.admin@example.com")
    admin_headers = await auth_headers(admin)
    country = await client.post("/api/v1/countries", json={"name": "Japan", "iso2": "JP"}, headers=admin_headers)
    university = await client.post(
        "/api/v1/universities",
        json={"country_id": country.json()["id"], "name": "Kyoto University"},
        headers=admin_headers,
    )
    program = await client.post(
        "/api/v1/programs",
        json={"university_id": university.json()["id"], "name": "MEng Robotics"},
        headers=admin_headers,
    )
    student = await user_factory(UserRole.STUDENT, email="activity.student@example.com")
    student_headers = await auth_headers(student)
    application = await client.post(
        APPLICATIONS,
        json={"student_id": str(student.id), "program_id": program.json()["id"]},
        headers=admin_headers,
    )
    return {
        "student": student,
        "headers": student_headers,
        "admin_headers": admin_headers,
        "application": application.json(),
    }


# ── Activity feed ────────────────────────────────────────────────────────────


async def test_activity_starts_empty(client: AsyncClient, student_with_application) -> None:
    response = await client.get(f"{STUDENT}/me/activity", headers=student_with_application["headers"])
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0}


async def test_a_document_upload_appears_in_the_feed(client: AsyncClient, student_with_application) -> None:
    student = student_with_application["student"]
    headers = student_with_application["headers"]

    await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=headers,
    )

    body = (await client.get(f"{STUDENT}/me/activity", headers=headers)).json()
    assert body["total"] == 1
    assert body["items"][0]["type"] == "document"
    assert "uploaded" in body["items"][0]["message"].lower()


async def test_an_application_status_change_appears_in_the_feed(client: AsyncClient, student_with_application) -> None:
    headers = student_with_application["headers"]
    application_id = student_with_application["application"]["id"]

    await client.post(
        f"{APPLICATIONS}/{application_id}/status",
        json={"status": "submitted"},
        headers=student_with_application["admin_headers"],
    )

    body = (await client.get(f"{STUDENT}/me/activity", headers=headers)).json()
    application_entries = [e for e in body["items"] if e["type"] == "application"]
    assert len(application_entries) == 1
    assert "submitted" in application_entries[0]["message"].lower()


async def test_status_change_is_not_duplicated_by_activity_log(client: AsyncClient, student_with_application) -> None:
    """The status-change subscriber also writes an ActivityLog row for the
    same event; the feed reads that transition from ApplicationStatusHistory
    only, so it should not appear twice."""
    headers = student_with_application["headers"]
    application_id = student_with_application["application"]["id"]

    await client.post(
        f"{APPLICATIONS}/{application_id}/status",
        json={"status": "submitted"},
        headers=student_with_application["admin_headers"],
    )

    body = (await client.get(f"{STUDENT}/me/activity", headers=headers)).json()
    assert body["total"] == 1


async def test_a_student_only_sees_their_own_activity(
    client: AsyncClient, student_with_application, user_factory, auth_headers
) -> None:
    student = student_with_application["student"]
    await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=student_with_application["headers"],
    )

    other = await user_factory(UserRole.STUDENT, email="activity.other@example.com")
    body = (await client.get(f"{STUDENT}/me/activity", headers=await auth_headers(other))).json()
    assert body == {"items": [], "total": 0}


# ── Preferences ──────────────────────────────────────────────────────────────


async def test_preferences_default_on_first_read(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="prefs.student@example.com")
    response = await client.get(f"{STUDENT}/me/preferences", headers=await auth_headers(student))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preferred_currency"] is None
    assert body["email_notifications_enabled"] is True
    assert body["push_notifications_enabled"] is True
    assert body["show_points_widget"] is True


async def test_updating_one_preference_leaves_the_others_untouched(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    student = await user_factory(UserRole.STUDENT, email="prefs.partial@example.com")
    headers = await auth_headers(student)

    response = await client.patch(f"{STUDENT}/me/preferences", json={"preferred_currency": "CAD"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preferred_currency"] == "CAD"
    assert body["show_points_widget"] is True


async def test_updating_repeatedly_reuses_the_same_row(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="prefs.idempotent@example.com")
    headers = await auth_headers(student)

    await client.patch(f"{STUDENT}/me/preferences", json={"show_points_widget": False}, headers=headers)
    response = await client.patch(
        f"{STUDENT}/me/preferences", json={"push_notifications_enabled": False}, headers=headers
    )
    body = response.json()
    assert body["show_points_widget"] is False
    assert body["push_notifications_enabled"] is False


async def test_a_student_only_sees_their_own_preferences(client: AsyncClient, user_factory, auth_headers) -> None:
    a = await user_factory(UserRole.STUDENT, email="prefs.a@example.com")
    b = await user_factory(UserRole.STUDENT, email="prefs.b@example.com")

    await client.patch(f"{STUDENT}/me/preferences", json={"preferred_currency": "GBP"}, headers=await auth_headers(a))
    b_body = (await client.get(f"{STUDENT}/me/preferences", headers=await auth_headers(b))).json()
    assert b_body["preferred_currency"] is None
