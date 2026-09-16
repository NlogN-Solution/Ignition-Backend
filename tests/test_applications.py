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


@pytest_asyncio.fixture
async def published_program_id(client: AsyncClient, user_factory, auth_headers) -> str:
    """A course a student may actually apply to.

    `program_id` above publishes nothing, which is fine for the staff endpoint
    — it takes any programme id — and wrong for `POST /student/me/applications`,
    which refuses an unpublished offering or one at an unpublished university.
    An application against a half-written catalogue record names a course no
    counsellor can act on.
    """
    admin = await user_factory(UserRole.ADMIN, email="published.admin@example.com")
    headers = await auth_headers(admin)

    country = (
        await client.post(f"{ACADEMIC}/countries", json={"name": "United Kingdom", "iso2": "GB"}, headers=headers)
    ).json()
    university = await client.post(
        f"{ACADEMIC}/universities",
        json={
            "country_id": country["id"],
            "name": "University of Hull",
            "slug": "hull-apps-test",
            "city": "Hull",
            "region": "England — North",
            "tagline": "A university in Hull",
            "overview": "A university in Hull.",
            "is_published": True,
        },
        headers=headers,
    )
    assert university.status_code == 200, university.text
    program = await client.post(
        f"{ACADEMIC}/programs",
        json={
            "university_id": university.json()["id"],
            "name": "MSc Data Science",
            "slug": "hull-msc-data-science-apps-test",
            "is_published": True,
        },
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


async def test_status_requirements_is_not_swallowed_by_the_id_route(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """`/status-requirements` is a literal segment under `/applications/{id}`.

    FastAPI matches in declaration order, and this route was declared *below*
    `GET /{application_id}` — so every call parsed "status-requirements" as a
    UUID and came back 422. The console fetches this to learn which statuses
    need a date and a letter; getting nothing, it believed none of them did,
    offered `offer_received` on the plain status dropdown, and showed the
    counsellor the backend's own refusal ("Use POST
    /applications/{id}/milestone") as though it were advice.

    The assertion that matters is the status code, not the payload: a 422 with
    `loc: ["path", "application_id"]` is the exact shape of the regression.
    """
    admin = await user_factory(UserRole.ADMIN)
    response = await client.get(
        f"{APPLICATIONS}/status-requirements", headers=await auth_headers(admin)
    )

    assert response.status_code == 200, response.text
    by_status = {item["status"]: item for item in response.json()}
    assert "offer_received" in by_status, "the offer milestone must reach the dialog"
    assert by_status["offer_received"]["required_date_field"] == "offer_received_date"
    assert by_status["offer_received"]["required_document"] == "offer_letter"


async def test_a_milestone_status_is_refused_on_the_plain_status_route(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    """The refusal the console must never have to show a human.

    It is correct and it stays — recording an offer with no date and no letter
    is what left applications claiming offers with nothing behind them. What
    changed is that the console can now see it coming, because
    `/status-requirements` resolves.
    """
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(counsellor)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, headers, student, program_id)

    refused = await client.post(
        f"{APPLICATIONS}/{application['id']}/status",
        json={"status": "offer_received"},
        headers=headers,
    )
    assert refused.status_code == 400
    assert "milestone" in refused.json()["detail"]


async def test_a_student_request_opens_as_requested_not_preparing(
    client: AsyncClient,
    user_factory,
    auth_headers,
    published_program_id: str,
) -> None:
    """`draft` is a commitment; a student pressing Apply has not been given one.

    Every rail in both frontends reads `draft` as "Preparing", so opening a
    student's own application there told them work had started and told the
    desk a file was in progress before anyone had looked at it.
    """
    student = await user_factory(UserRole.STUDENT, email="requester@example.com")
    headers = await auth_headers(student)

    created = await client.post(
        "/api/v1/student/me/applications", json={"program_id": published_program_id}, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "requested"


async def test_a_student_cannot_submit_an_unaccepted_request(
    client: AsyncClient,
    user_factory,
    auth_headers,
    published_program_id: str,
) -> None:
    """You cannot hand over a file nobody has picked up."""
    student = await user_factory(UserRole.STUDENT, email="eager@example.com")
    headers = await auth_headers(student)
    created = (
        await client.post(
            "/api/v1/student/me/applications", json={"program_id": published_program_id}, headers=headers
        )
    ).json()

    refused = await client.post(
        f"/api/v1/student/me/applications/{created['id']}/submit", headers=headers
    )
    assert refused.status_code == 400
    assert "not accepted" in refused.json()["detail"]


async def test_staff_created_applications_start_at_draft(
    client: AsyncClient,
    user_factory,
    auth_headers,
    program_id: str,
) -> None:
    """A counsellor opening a file has already decided to work it."""
    counsellor = await user_factory(UserRole.COUNSELLOR)
    student = await user_factory(UserRole.STUDENT)
    application = await _create(client, await auth_headers(counsellor), student, program_id)
    assert application["status"] == "draft"


async def test_accepting_a_request_moves_it_to_draft_and_is_recorded(
    client: AsyncClient,
    user_factory,
    auth_headers,
    published_program_id: str,
) -> None:
    """Acceptance is a commitment, so it lands in the audit trail like any other
    transition — "who agreed to work this, and when" is what the queue exists to
    answer."""
    student = await user_factory(UserRole.STUDENT, email="accepted@example.com")
    created = (
        await client.post(
            "/api/v1/student/me/applications",
            json={"program_id": published_program_id},
            headers=await auth_headers(student),
        )
    ).json()

    counsellor = await user_factory(UserRole.COUNSELLOR)
    staff_headers = await auth_headers(counsellor)
    accepted = await client.post(f"{APPLICATIONS}/{created['id']}/accept", headers=staff_headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "draft"

    history = (
        await client.get(f"{APPLICATIONS}/{created['id']}/status-history", headers=staff_headers)
    ).json()
    assert history[-1]["old_status"] == "requested"
    assert history[-1]["new_status"] == "draft"
    assert history[-1]["changed_by"] == str(counsellor.id)

    # A second accept must not rewind anything that has moved on.
    await client.post(
        f"{APPLICATIONS}/{created['id']}/status",
        json={"status": "submitted"},
        headers=staff_headers,
    )
    again = await client.post(f"{APPLICATIONS}/{created['id']}/accept", headers=staff_headers)
    assert again.status_code == 200
    assert again.json()["status"] == "submitted", "accepting twice must not reopen a moved file"
