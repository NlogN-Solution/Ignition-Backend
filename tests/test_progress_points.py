"""Phase 6 — progress milestones and the points ledger.

Both advance from events only. The tests therefore drive them the way the system
does — by approving a document or moving an application through the staff API —
rather than by calling the services directly, so the subscriber wiring is part
of what is verified.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models import PointsRule, ProgressMilestone
from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"
APPLICATIONS = "/api/v1/applications"

MILESTONES = [
    ("profile", "Profile complete", 10, 1),
    ("documents", "Core documents uploaded", 15, 2),
    ("apply", "Application submitted", 20, 3),
    ("offer", "Offer received", 15, 4),
]
POINTS_RULES = [
    ("document.upload", "Document approved", 15, False),
    ("application.submit", "Application submitted", 50, False),
]


@pytest_asyncio.fixture
async def catalog(session) -> None:
    """The ladder and the rules — normally created by scripts/seed.py."""
    for order, (key, label, weight, _) in enumerate(MILESTONES, start=1):
        session.add(ProgressMilestone(key=key, label=label, weight=weight, order=order))
    for action, label, points, once in POINTS_RULES:
        session.add(PointsRule(action=action, label=label, points=points, once_per_student=once))
    await session.commit()


@pytest_asyncio.fixture
async def student_with_application(client: AsyncClient, user_factory, auth_headers, catalog) -> dict:
    admin = await user_factory(UserRole.ADMIN, email="pp.admin@example.com")
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

    student = await user_factory(UserRole.STUDENT, email="pp.student@example.com")
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


# ── Progress ──────────────────────────────────────────────────────────────────


async def test_progress_starts_empty_but_lists_the_whole_ladder(
    client: AsyncClient,
    student_with_application,
) -> None:
    """The portal shows what is still ahead, so unreached milestones are part of
    the response rather than absent from it."""
    response = await client.get(f"{STUDENT}/me/progress", headers=student_with_application["headers"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["completion_percentage"] == 0
    assert [m["key"] for m in body["milestones"]] == ["profile", "documents", "apply", "offer"]
    assert all(m["is_complete"] is False for m in body["milestones"])
    assert body["next_milestone"]["key"] == "profile"


async def test_approving_a_document_advances_progress(
    client: AsyncClient,
    student_with_application,
) -> None:
    student = student_with_application["student"]
    headers = student_with_application["headers"]

    uploaded = await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text

    # Uploading is not enough — a counsellor has to approve it.
    before = (await client.get(f"{STUDENT}/me/progress", headers=headers)).json()
    assert before["completion_percentage"] == 0

    await client.post(
        f"/api/v1/documents/{uploaded.json()['id']}/verify",
        json={},
        headers=student_with_application["admin_headers"],
    )

    after = (await client.get(f"{STUDENT}/me/progress", headers=headers)).json()
    documents = next(m for m in after["milestones"] if m["key"] == "documents")
    assert documents["is_complete"] is True
    assert documents["completed_at"] is not None
    # 15 of 60 total weight.
    assert after["completion_percentage"] == 25


async def test_progress_is_weighted_not_counted(client: AsyncClient, student_with_application) -> None:
    """ "Offer received" must be worth more than "profile complete", so the
    percentage cannot be a simple count of completed rows."""
    headers = student_with_application["headers"]
    application_id = student_with_application["application"]["id"]

    await client.post(
        f"{APPLICATIONS}/{application_id}/status",
        json={"status": "submitted"},
        headers=student_with_application["admin_headers"],
    )

    body = (await client.get(f"{STUDENT}/me/progress", headers=headers)).json()
    # `apply` weighs 20 of 60, not 1 of 4.
    assert body["completion_percentage"] == 33
    assert next(m for m in body["milestones"] if m["key"] == "apply")["is_complete"] is True


async def test_a_student_cannot_see_another_students_progress(
    client: AsyncClient,
    student_with_application,
    user_factory,
    auth_headers,
) -> None:
    await client.post(
        f"{APPLICATIONS}/{student_with_application['application']['id']}/status",
        json={"status": "submitted"},
        headers=student_with_application["admin_headers"],
    )

    other = await user_factory(UserRole.STUDENT, email="pp.other@example.com")
    body = (await client.get(f"{STUDENT}/me/progress", headers=await auth_headers(other))).json()
    assert body["completion_percentage"] == 0


# ── Points ────────────────────────────────────────────────────────────────────


async def test_points_are_awarded_by_events_not_by_the_client(
    client: AsyncClient,
    student_with_application,
) -> None:
    headers = student_with_application["headers"]
    assert (await client.get(f"{STUDENT}/me/points", headers=headers)).json()["balance"] == 0

    await client.post(
        f"{APPLICATIONS}/{student_with_application['application']['id']}/status",
        json={"status": "submitted"},
        headers=student_with_application["admin_headers"],
    )

    body = (await client.get(f"{STUDENT}/me/points", headers=headers)).json()
    assert body["balance"] == 50
    assert body["history"][0]["action"] == "application.submit"


async def test_there_is_no_endpoint_to_award_yourself_points(client: AsyncClient, student_with_application) -> None:
    """The ledger is only ever written by subscribers — a student posting to the
    points path must not find a handler there."""
    response = await client.post(
        f"{STUDENT}/me/points",
        json={"action": "application.submit", "points": 9999},
        headers=student_with_application["headers"],
    )
    assert response.status_code in {404, 405}


async def test_the_same_document_cannot_be_paid_for_twice(
    client: AsyncClient,
    student_with_application,
) -> None:
    """Re-approving an already approved document must not pay again — the
    ledger has a uniqueness constraint on (student, action, reference)."""
    student = student_with_application["student"]
    headers = student_with_application["headers"]
    admin_headers = student_with_application["admin_headers"]

    uploaded = await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=headers,
    )
    document_id = uploaded.json()["id"]

    for _ in range(3):
        await client.post(f"/api/v1/documents/{document_id}/verify", json={}, headers=admin_headers)

    body = (await client.get(f"{STUDENT}/me/points", headers=headers)).json()
    assert body["balance"] == 15, "one document, one award"
    assert len([e for e in body["history"] if e["action"] == "document.upload"]) == 1


async def test_two_different_documents_each_earn_points(
    client: AsyncClient,
    student_with_application,
) -> None:
    student = student_with_application["student"]
    headers = student_with_application["headers"]
    admin_headers = student_with_application["admin_headers"]

    for document_type in ("passport", "academic_transcript"):
        uploaded = await client.post(
            "/api/v1/documents/upload",
            data={"student_id": str(student.id), "document_type": document_type},
            files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
        await client.post(f"/api/v1/documents/{uploaded.json()['id']}/verify", json={}, headers=admin_headers)

    assert (await client.get(f"{STUDENT}/me/points", headers=headers)).json()["balance"] == 30


async def test_completion_is_zero_when_no_milestones_are_configured(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """A fresh database must not divide by zero on the dashboard."""
    student = await user_factory(UserRole.STUDENT, email="pp.empty@example.com")
    body = (await client.get(f"{STUDENT}/me/progress", headers=await auth_headers(student))).json()
    assert body["completion_percentage"] == 0
    assert body["milestones"] == []
    assert body["next_milestone"] is None
