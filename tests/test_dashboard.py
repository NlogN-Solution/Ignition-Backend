"""Phase 6 — the dashboard aggregation endpoint.

Everything the dashboard shows already has its own endpoint and its own
tests; these tests check the fan-out and the filtering rules specific to the
aggregate view. Caching is exercised against a small in-memory fake Redis
client rather than a live one, so the suite never needs Redis running — the
service only talks to `app.core.cache`'s module-level functions, which this
fixture redirects to the fake.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"
APPLICATIONS = "/api/v1/applications"


class FakeRedis:
    """The subset of the redis-py async API `app.core.cache` calls."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def fake_cache(monkeypatch):
    from app.core import cache as cache_module

    fake = FakeRedis()
    monkeypatch.setattr(cache_module, "_get_client", lambda: fake)
    return fake


@pytest_asyncio.fixture
async def student_with_application(client: AsyncClient, user_factory, auth_headers) -> dict:
    admin = await user_factory(UserRole.ADMIN, email="dash.admin@example.com")
    admin_headers = await auth_headers(admin)
    country = await client.post("/api/v1/countries", json={"name": "Sweden", "iso2": "SE"}, headers=admin_headers)
    university = await client.post(
        "/api/v1/universities",
        json={"country_id": country.json()["id"], "name": "Lund University"},
        headers=admin_headers,
    )
    program = await client.post(
        "/api/v1/programs",
        json={"university_id": university.json()["id"], "name": "MSc Robotics"},
        headers=admin_headers,
    )
    student = await user_factory(UserRole.STUDENT, email="dash.student@example.com")
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


async def test_dashboard_shape_for_a_fresh_student(client: AsyncClient, student_with_application, fake_cache) -> None:
    response = await client.get(f"{STUDENT}/me/dashboard", headers=student_with_application["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["completion_percentage"] == 0
    assert body["points_balance"] == 0
    assert body["pending_documents_count"] == 0
    assert body["unread_notifications_count"] == 0
    assert body["upcoming_appointments"] == []
    assert body["recent_activity"] == []
    # A DRAFT application (the default on creation) is still active.
    assert len(body["active_applications"]) == 1


async def test_withdrawn_applications_are_excluded_from_active(
    client: AsyncClient, student_with_application, fake_cache
) -> None:
    application_id = student_with_application["application"]["id"]
    await client.post(
        f"{APPLICATIONS}/{application_id}/status",
        json={"status": "withdrawn"},
        headers=student_with_application["admin_headers"],
    )

    body = (await client.get(f"{STUDENT}/me/dashboard", headers=student_with_application["headers"])).json()
    assert body["active_applications"] == []


async def test_pending_documents_are_counted(client: AsyncClient, student_with_application, fake_cache) -> None:
    student = student_with_application["student"]
    await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=student_with_application["headers"],
    )

    body = (await client.get(f"{STUDENT}/me/dashboard", headers=student_with_application["headers"])).json()
    assert body["pending_documents_count"] == 1


async def test_a_second_request_is_served_from_cache(client: AsyncClient, student_with_application, fake_cache) -> None:
    headers = student_with_application["headers"]
    first = await client.get(f"{STUDENT}/me/dashboard", headers=headers)
    assert first.json()["cached"] is False

    second = await client.get(f"{STUDENT}/me/dashboard", headers=headers)
    assert second.json()["cached"] is True
    # The cached copy reflects what was true when it was written.
    assert second.json()["pending_documents_count"] == first.json()["pending_documents_count"]


async def test_a_document_upload_invalidates_the_cache(
    client: AsyncClient, student_with_application, fake_cache
) -> None:
    """The whole point of event-driven invalidation: a student sees the
    consequence of an action immediately, not up to the TTL later."""
    student = student_with_application["student"]
    headers = student_with_application["headers"]

    first = await client.get(f"{STUDENT}/me/dashboard", headers=headers)
    assert first.json()["pending_documents_count"] == 0

    await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=headers,
    )

    second = await client.get(f"{STUDENT}/me/dashboard", headers=headers)
    assert second.json()["cached"] is False
    assert second.json()["pending_documents_count"] == 1


async def test_a_student_cannot_see_another_students_dashboard(
    client: AsyncClient, student_with_application, user_factory, auth_headers, fake_cache
) -> None:
    student = student_with_application["student"]
    await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=student_with_application["headers"],
    )

    other = await user_factory(UserRole.STUDENT, email="dash.other@example.com")
    body = (await client.get(f"{STUDENT}/me/dashboard", headers=await auth_headers(other))).json()
    assert body["pending_documents_count"] == 0
    assert body["active_applications"] == []


async def test_dashboard_degrades_gracefully_when_redis_is_unreachable(
    client: AsyncClient, student_with_application
) -> None:
    """No `fake_cache` fixture here — this hits whatever `REDIS_HOST` the test
    settings point at, which is not running. The endpoint must still succeed,
    uncached, rather than 500."""
    response = await client.get(f"{STUDENT}/me/dashboard", headers=student_with_application["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["cached"] is False
