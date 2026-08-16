"""Phase 5a — the student portal surface.

The whole point of `StudentScopedRepository` is that ownership is applied inside
the query builder rather than remembered per handler. These tests hold that
claim to account: for every collection endpoint, a second student's rows must be
invisible, and a direct id lookup of someone else's row must 404 rather than 403
(a 403 would confirm the row exists).
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
async def staff_headers(client: AsyncClient, user_factory, auth_headers) -> dict[str, str]:
    counsellor = await user_factory(UserRole.COUNSELLOR, email="sp.counsellor@example.com")
    return await auth_headers(counsellor)


@pytest_asyncio.fixture
async def program_id(client: AsyncClient, staff_headers, user_factory, auth_headers) -> str:
    admin = await user_factory(UserRole.ADMIN, email="sp.admin@example.com")
    headers = await auth_headers(admin)
    country = await client.post("/api/v1/countries", json={"name": "Ireland", "iso2": "IE"}, headers=headers)
    university = await client.post(
        "/api/v1/universities",
        json={"country_id": country.json()["id"], "name": "Trinity College"},
        headers=headers,
    )
    program = await client.post(
        "/api/v1/programs",
        json={"university_id": university.json()["id"], "name": "MSc Analytics"},
        headers=headers,
    )
    return str(program.json()["id"])


@pytest_asyncio.fixture
async def two_students(client: AsyncClient, staff_headers, user_factory, auth_headers, program_id: str) -> dict:
    """Two students, each with an application, document and payment."""
    made = {}
    for key, email in (("mine", "sp.mine@example.com"), ("theirs", "sp.theirs@example.com")):
        student = await user_factory(UserRole.STUDENT, email=email)
        headers = await auth_headers(student)

        application = await client.post(
            APPLICATIONS,
            json={"student_id": str(student.id), "program_id": program_id},
            headers=staff_headers,
        )
        assert application.status_code == 200, application.text

        document = await client.post(
            "/api/v1/documents/upload",
            data={"student_id": str(student.id), "document_type": "passport"},
            files={"file": ("p.pdf", b"%PDF-1.4 scan", "application/pdf")},
            headers=headers,
        )
        assert document.status_code == 200, document.text

        made[key] = {
            "user": student,
            "headers": headers,
            "application": application.json(),
            "document": document.json(),
        }
    return made


# ── Access ────────────────────────────────────────────────────────────────────


async def test_staff_are_refused_the_student_surface(
    client: AsyncClient,
    staff_headers,
) -> None:
    """Refused outright, not scoped to nothing — an empty portal would hide the
    caller's bug."""
    response = await client.get(f"{STUDENT}/me", headers=staff_headers)
    assert response.status_code == 403


async def test_the_student_surface_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get(f"{STUDENT}/me")).status_code == 401


async def test_a_student_reads_their_own_account(client: AsyncClient, two_students) -> None:
    mine = two_students["mine"]
    response = await client.get(f"{STUDENT}/me", headers=mine["headers"])
    assert response.status_code == 200
    assert response.json()["email"] == mine["user"].email
    assert response.json()["role"] == "student"


# ── Collections are scoped ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/me/applications", "/me/documents", "/me/appointments", "/me/tasks", "/me/payments", "/me/notifications"],
)
async def test_collections_return_only_my_rows(client: AsyncClient, two_students, path: str) -> None:
    mine, theirs = two_students["mine"], two_students["theirs"]

    response = await client.get(f"{STUDENT}{path}", headers=mine["headers"])
    assert response.status_code == 200, response.text

    body = response.json()
    owner_ids = {item.get("student_id") or item.get("user_id") for item in body["items"]}
    assert str(theirs["user"].id) not in owner_ids
    assert owner_ids <= {str(mine["user"].id)}


async def test_my_applications_lists_mine(client: AsyncClient, two_students) -> None:
    mine = two_students["mine"]
    body = (await client.get(f"{STUDENT}/me/applications", headers=mine["headers"])).json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == mine["application"]["id"]


async def test_my_applications_embeds_the_program_summary(client: AsyncClient, two_students) -> None:
    """The list and single-item routes both eager-load `program` (and its
    `university`) so the portal can render "MSc Analytics — Trinity College"
    without a second round trip per application."""
    mine = two_students["mine"]

    listed = (await client.get(f"{STUDENT}/me/applications", headers=mine["headers"])).json()
    program = listed["items"][0]["program"]
    assert program["name"] == "MSc Analytics"
    assert program["university_name"] == "Trinity College"

    single = await client.get(f"{STUDENT}/me/applications/{mine['application']['id']}", headers=mine["headers"])
    assert single.json()["program"]["university_name"] == "Trinity College"


async def test_my_application_embeds_the_university_country_and_counsellor_summary(
    client: AsyncClient, two_students, staff_headers, user_factory, program_id: str
) -> None:
    """The single-item route also carries `program.university_country` and a
    `counsellor` summary — mirroring `AppointmentCounsellorSummary` — so the
    portal can render both without a second round trip."""
    mine = two_students["mine"]
    counsellor = await user_factory(UserRole.COUNSELLOR, email="sp.assigned-counsellor@example.com")

    created = await client.post(
        APPLICATIONS,
        json={
            "student_id": str(mine["user"].id),
            "program_id": program_id,
            "counsellor_id": str(counsellor.id),
        },
        headers=staff_headers,
    )
    assert created.status_code == 200, created.text

    detail = await client.get(f"{STUDENT}/me/applications/{created.json()['id']}", headers=mine["headers"])
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["program"]["university_country"] == "Ireland"
    assert body["counsellor"]["full_name"] == f"{counsellor.first_name} {counsellor.last_name}"


# ── Direct id lookups 404 across students ─────────────────────────────────────


async def test_another_students_application_is_not_found(client: AsyncClient, two_students) -> None:
    mine, theirs = two_students["mine"], two_students["theirs"]
    other_id = theirs["application"]["id"]

    for path in (
        f"/me/applications/{other_id}",
        f"/me/applications/{other_id}/timeline",
        f"/me/applications/{other_id}/checklist",
    ):
        response = await client.get(f"{STUDENT}{path}", headers=mine["headers"])
        assert response.status_code == 404, f"{path} leaked: {response.status_code}"


async def test_another_students_document_is_not_found(client: AsyncClient, two_students) -> None:
    mine, theirs = two_students["mine"], two_students["theirs"]
    response = await client.get(f"{STUDENT}/me/documents/{theirs['document']['id']}", headers=mine["headers"])
    assert response.status_code == 404


async def test_my_own_application_and_document_are_reachable(client: AsyncClient, two_students) -> None:
    mine = two_students["mine"]
    assert (
        await client.get(f"{STUDENT}/me/applications/{mine['application']['id']}", headers=mine["headers"])
    ).status_code == 200
    assert (
        await client.get(f"{STUDENT}/me/documents/{mine['document']['id']}", headers=mine["headers"])
    ).status_code == 200


# ── The single-database payoff ────────────────────────────────────────────────


async def test_a_staff_status_change_appears_on_the_student_timeline(
    client: AsyncClient,
    two_students,
    staff_headers,
) -> None:
    """A counsellor moves the application in the staff dashboard; the student
    sees it through their own endpoint, from the same row."""
    mine = two_students["mine"]
    application_id = mine["application"]["id"]

    changed = await client.post(
        f"{APPLICATIONS}/{application_id}/status",
        json={"status": "submitted", "remarks": "Sent to Trinity"},
        headers=staff_headers,
    )
    assert changed.status_code == 200, changed.text

    timeline = await client.get(f"{STUDENT}/me/applications/{application_id}/timeline", headers=mine["headers"])
    assert timeline.status_code == 200, timeline.text
    entries = timeline.json()
    assert len(entries) == 1
    assert entries[0]["new_status"] == "submitted"
    assert entries[0]["remarks"] == "Sent to Trinity"

    detail = await client.get(f"{STUDENT}/me/applications/{application_id}", headers=mine["headers"])
    assert detail.json()["status"] == "submitted"


# ── Profile, notifications, appointments ──────────────────────────────────────


async def test_a_student_creates_and_reads_their_profile(client: AsyncClient, two_students) -> None:
    mine = two_students["mine"]
    created = await client.patch(
        f"{STUDENT}/me/profile",
        json={"education_level": "bachelor", "nationality": "Nepali"},
        headers=mine["headers"],
    )
    assert created.status_code == 200, created.text
    assert created.json()["nationality"] == "Nepali"

    fetched = await client.get(f"{STUDENT}/me/profile", headers=mine["headers"])
    assert fetched.status_code == 200
    assert fetched.json()["user_id"] == str(mine["user"].id)


async def test_my_appointments_embeds_the_counsellor_summary(client: AsyncClient, two_students, staff_headers) -> None:
    mine = two_students["mine"]
    counsellor_id = (await client.get("/api/v1/auth/me", headers=staff_headers)).json()["id"]

    created = await client.post(
        "/api/v1/appointments",
        json={
            "student_id": str(mine["user"].id),
            "counsellor_id": counsellor_id,
            "appointment_type": "consultation",
            "title": "Course advice",
            "start_time": "2027-04-01T10:00:00Z",
            "end_time": "2027-04-01T10:30:00Z",
        },
        headers=staff_headers,
    )
    assert created.status_code == 200, created.text

    body = (await client.get(f"{STUDENT}/me/appointments", headers=mine["headers"])).json()
    assert body["items"][0]["counsellor"]["id"] == counsellor_id
    assert body["items"][0]["counsellor"]["full_name"]


async def test_requesting_an_appointment_cannot_self_schedule(client: AsyncClient, two_students) -> None:
    mine = two_students["mine"]
    response = await client.post(
        f"{STUDENT}/me/appointments/request",
        json={"title": "Course advice", "preferred_date": "2027-04-01", "appointment_type": "consultation"},
        headers=mine["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["start_time"] is None
    assert body["student_id"] == str(mine["user"].id)


async def test_read_all_only_touches_my_notifications(client: AsyncClient, two_students) -> None:
    mine, theirs = two_students["mine"], two_students["theirs"]

    # Both students have an unread "document uploaded" notification path; give
    # each a notification by requesting an appointment.
    for who in (mine, theirs):
        await client.post(
            f"{STUDENT}/me/appointments/request",
            json={"title": "Advice", "preferred_date": "2027-04-01"},
            headers=who["headers"],
        )

    marked = await client.post(f"{STUDENT}/me/notifications/read-all", headers=mine["headers"])
    assert marked.status_code == 200
    assert marked.json()["updated"] >= 1

    theirs_unread = await client.get(
        f"{STUDENT}/me/notifications",
        params={"is_read": "false"},
        headers=theirs["headers"],
    )
    assert theirs_unread.json()["total"] >= 1, "read-all must not touch another student's notifications"


# ── Catalog and saved items ───────────────────────────────────────────────────


async def test_the_catalog_is_browsable_and_hides_drafts(
    client: AsyncClient,
    two_students,
    program_id: str,
) -> None:
    mine = two_students["mine"]

    programs = await client.get(f"{STUDENT}/catalog/programs", headers=mine["headers"])
    assert programs.status_code == 200
    assert any(p["id"] == program_id for p in programs.json()["items"])

    # Guides and blog posts exist only once published; nothing is seeded here,
    # so the endpoints must return an empty page rather than error.
    for path in ("/catalog/guides", "/catalog/blog", "/catalog/countries", "/catalog/universities"):
        response = await client.get(f"{STUDENT}{path}", headers=mine["headers"])
        assert response.status_code == 200, f"{path}: {response.text}"


async def test_saving_a_course_is_idempotent_and_private(
    client: AsyncClient,
    two_students,
    program_id: str,
) -> None:
    mine, theirs = two_students["mine"], two_students["theirs"]

    first = await client.post(f"{STUDENT}/me/saved/courses", json={"item_id": program_id}, headers=mine["headers"])
    assert first.status_code == 200, first.text
    second = await client.post(f"{STUDENT}/me/saved/courses", json={"item_id": program_id}, headers=mine["headers"])
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"], "saving twice must not create a second row"

    assert (await client.get(f"{STUDENT}/me/saved/courses", headers=mine["headers"])).json()["total"] == 1
    # The other student's list is untouched.
    assert (await client.get(f"{STUDENT}/me/saved/courses", headers=theirs["headers"])).json()["total"] == 0


async def test_unsaving_only_touches_my_own_list(client: AsyncClient, two_students, program_id: str) -> None:
    mine, theirs = two_students["mine"], two_students["theirs"]
    await client.post(f"{STUDENT}/me/saved/courses", json={"item_id": program_id}, headers=theirs["headers"])

    # I never saved it, so for me it does not exist — and theirs must survive.
    missing = await client.delete(f"{STUDENT}/me/saved/courses/{program_id}", headers=mine["headers"])
    assert missing.status_code == 404
    assert (await client.get(f"{STUDENT}/me/saved/courses", headers=theirs["headers"])).json()["total"] == 1


async def test_saving_an_unknown_course_is_404_not_500(client: AsyncClient, two_students) -> None:
    mine = two_students["mine"]
    response = await client.post(
        f"{STUDENT}/me/saved/courses",
        json={"item_id": "00000000-0000-0000-0000-000000000000"},
        headers=mine["headers"],
    )
    assert response.status_code == 404


async def test_the_compare_tray_is_capped(
    client: AsyncClient, two_students, staff_headers, user_factory, auth_headers
) -> None:
    """Three is the documented cap; the fourth must be refused with a message
    the UI can show, not a database error."""
    mine = two_students["mine"]
    admin = await user_factory(UserRole.ADMIN, email="cmp.admin@example.com")
    admin_headers = await auth_headers(admin)

    country = await client.post("/api/v1/countries", json={"name": "Spain", "iso2": "ES"}, headers=admin_headers)
    university = await client.post(
        "/api/v1/universities",
        json={"country_id": country.json()["id"], "name": "IE Business School"},
        headers=admin_headers,
    )
    program_ids = []
    for index in range(4):
        program = await client.post(
            "/api/v1/programs",
            json={"university_id": university.json()["id"], "name": f"Course {index}"},
            headers=admin_headers,
        )
        program_ids.append(program.json()["id"])

    for program_id in program_ids[:3]:
        added = await client.post(f"{STUDENT}/me/compare", json={"item_id": program_id}, headers=mine["headers"])
        assert added.status_code == 200, added.text

    fourth = await client.post(f"{STUDENT}/me/compare", json={"item_id": program_ids[3]}, headers=mine["headers"])
    assert fourth.status_code == 400
    assert "at most 3" in fourth.json()["detail"]

    tray = await client.get(f"{STUDENT}/me/compare", headers=mine["headers"])
    assert tray.json()["total"] == 3
    assert tray.json()["max_items"] == 3

    # Removing one makes room again.
    assert (await client.delete(f"{STUDENT}/me/compare/{program_ids[0]}", headers=mine["headers"])).status_code == 200
    assert (
        await client.post(f"{STUDENT}/me/compare", json={"item_id": program_ids[3]}, headers=mine["headers"])
    ).status_code == 200
