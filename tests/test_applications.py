"""Application endpoints.

Two ED360 defects drive most of these tests: `POST /applications` is guarded by
a bare `get_current_user`, and `PATCH` accepts `status` and writes it without a
history row.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

APPLICATIONS = "/api/v1/applications"
ACADEMIC = "/api/v1"


@pytest_asyncio.fixture
async def program_id(client: AsyncClient, user_factory, auth_headers) -> str:
    """A program to apply to, created through the catalog API."""
    admin = await user_factory(UserRole.ADMIN, email="catalog.admin@example.com")
    headers = await auth_headers(admin)

    country = await client.post(
        f"{ACADEMIC}/countries",
        json={"name": "Australia", "iso2": "AU"},
        headers=headers,
    )
    university = await client.post(
        f"{ACADEMIC}/universities",
        json={"country_id": country.json()["id"], "name": "University of Melbourne"},
        headers=headers,
    )
    program = await client.post(
        f"{ACADEMIC}/programs",
        json={"university_id": university.json()["id"], "name": "MSc Computer Science"},
        headers=headers,
    )
    assert program.status_code == 200, program.text
    return str(program.json()["id"])


async def _create(client: AsyncClient, headers: dict[str, str], student, program_id: str, **overrides) -> dict:
    payload = {"student_id": str(student.id), "program_id": program_id}
    payload.update(overrides)
    response = await client.post(APPLICATIONS, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── Creation is staff-only ────────────────────────────────────────────────────


async def test_a_student_cannot_create_an_application(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    """ED360 guards this with `get_current_user`, so a student can file an
    application naming any `student_id` and any starting `status`."""
    student = await user_factory(UserRole.STUDENT)
    victim = await user_factory(UserRole.STUDENT)

    response = await client.post(
        APPLICATIONS,
        json={"student_id": str(victim.id), "program_id": program_id, "status": "enrolled"},
        headers=await auth_headers(student),
    )
    assert response.status_code == 403


async def test_a_counsellor_creates_an_application(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    student = await user_factory(UserRole.STUDENT)

    created = await _create(client, await auth_headers(counsellor), student, program_id)
    assert created["student_id"] == str(student.id)
    assert created["status"] == "draft"


# ── Students see only their own ───────────────────────────────────────────────


async def test_a_student_only_lists_their_own_applications(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    staff_headers = await auth_headers(counsellor)

    mine = await user_factory(UserRole.STUDENT)
    theirs = await user_factory(UserRole.STUDENT)
    await _create(client, staff_headers, mine, program_id)
    await _create(client, staff_headers, theirs, program_id)

    response = await client.get(APPLICATIONS, headers=await auth_headers(mine))
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["student_id"] == str(mine.id)


async def test_a_student_cannot_widen_the_list_with_student_id(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    theirs = await user_factory(UserRole.STUDENT)
    await _create(client, await auth_headers(counsellor), theirs, program_id)

    intruder = await user_factory(UserRole.STUDENT)
    response = await client.get(
        APPLICATIONS,
        params={"student_id": str(theirs.id)},
        headers=await auth_headers(intruder),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_a_student_cannot_read_another_students_application(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    owner = await user_factory(UserRole.STUDENT)
    application = await _create(client, await auth_headers(counsellor), owner, program_id)

    intruder = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(intruder)
    assert (await client.get(f"{APPLICATIONS}/{application['id']}", headers=headers)).status_code == 403
    assert (await client.get(f"{APPLICATIONS}/{application['id']}/status-history", headers=headers)).status_code == 403


async def test_a_student_reads_their_own_application(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, await auth_headers(counsellor), student, program_id)

    response = await client.get(f"{APPLICATIONS}/{application['id']}", headers=await auth_headers(student))
    assert response.status_code == 200


# ── Status changes are auditable ──────────────────────────────────────────────


async def test_status_changes_are_recorded(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(counsellor)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, headers, student, program_id)

    changed = await client.post(
        f"{APPLICATIONS}/{application['id']}/status",
        json={"status": "submitted", "remarks": "Sent to university"},
        headers=headers,
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "submitted"

    history = await client.get(f"{APPLICATIONS}/{application['id']}/status-history", headers=headers)
    assert len(history.json()) == 1
    entry = history.json()[0]
    assert entry["old_status"] == "draft"
    assert entry["new_status"] == "submitted"
    assert entry["changed_by"] == str(counsellor.id)
    assert entry["remarks"] == "Sent to university"


async def test_patch_cannot_change_status_behind_the_audit_trail(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    """ED360's `ApplicationUpdate` carries `status`, so PATCH moves an
    application without recording history. Here the field does not exist and
    `extra` defaults to ignore, so the status simply stays put."""
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(counsellor)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, headers, student, program_id)

    patched = await client.patch(
        f"{APPLICATIONS}/{application['id']}",
        json={"status": "enrolled", "remarks": "note"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "draft"
    assert patched.json()["remarks"] == "note"

    history = await client.get(f"{APPLICATIONS}/{application['id']}/status-history", headers=headers)
    assert history.json() == []


async def test_a_repeated_status_writes_no_duplicate_history(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(counsellor)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, headers, student, program_id)

    for _ in range(2):
        await client.post(
            f"{APPLICATIONS}/{application['id']}/status",
            json={"status": "submitted"},
            headers=headers,
        )

    history = await client.get(f"{APPLICATIONS}/{application['id']}/status-history", headers=headers)
    assert len(history.json()) == 1


async def test_students_cannot_change_status(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, await auth_headers(counsellor), student, program_id)

    response = await client.post(
        f"{APPLICATIONS}/{application['id']}/status",
        json={"status": "enrolled"},
        headers=await auth_headers(student),
    )
    assert response.status_code == 403


# ── Deletion ──────────────────────────────────────────────────────────────────


async def test_only_admins_delete_applications(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, await auth_headers(counsellor), student, program_id)

    assert (
        await client.delete(f"{APPLICATIONS}/{application['id']}", headers=await auth_headers(counsellor))
    ).status_code == 403

    admin = await user_factory(UserRole.ADMIN)
    assert (
        await client.delete(f"{APPLICATIONS}/{application['id']}", headers=await auth_headers(admin))
    ).status_code == 200
