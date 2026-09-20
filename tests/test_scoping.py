"""Who sees whose records.

Every list in the console used to be the whole book of business: a counsellor
opening Leads saw every lead in the agency, Applications showed every file, and
`/messages/threads` returned every student's support conversation to any of six
staff roles. Role checks answered "may you use this screen" and nothing
answered "which rows".

These tests pin the rule described in `app/api/scoping.py` and
`app/services/message_scope.py`: **own work, plus anything unclaimed.** They are
written so that a regression shows up as a test naming the exact disclosure,
rather than as a count that happens to change.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

LEADS = "/api/v1/leads"
APPLICATIONS = "/api/v1/applications"
MESSAGES = "/api/v1/messages"
ACADEMIC = "/api/v1"


@pytest_asyncio.fixture
async def program_id(client: AsyncClient, user_factory, auth_headers) -> str:
    admin = await user_factory(UserRole.ADMIN, email="scope.catalog@example.com")
    headers = await auth_headers(admin)
    country = await client.post(f"{ACADEMIC}/countries", json={"name": "Wales", "iso2": "WL"}, headers=headers)
    university = await client.post(
        f"{ACADEMIC}/universities",
        json={"country_id": country.json()["id"], "name": "Cardiff University"},
        headers=headers,
    )
    program = await client.post(
        f"{ACADEMIC}/programs",
        json={"university_id": university.json()["id"], "name": "MSc Marine Biology"},
        headers=headers,
    )
    return str(program.json()["id"])


async def _lead(client, headers, name: str, assigned_to=None) -> dict:
    payload = {"first_name": name, "phone": f"+97798{abs(hash(name)) % 100000000:08d}"}
    if assigned_to:
        payload["assigned_to"] = str(assigned_to)
    response = await client.post(LEADS, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── Leads ─────────────────────────────────────────────────────────────────────


async def test_a_counsellor_does_not_see_another_counsellors_leads(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    admin = await user_factory(UserRole.ADMIN, email="scope.admin@example.com")
    mine = await user_factory(UserRole.COUNSELLOR, email="scope.mine@example.com")
    theirs = await user_factory(UserRole.COUNSELLOR, email="scope.theirs@example.com")
    admin_headers = await auth_headers(admin)

    ours = await _lead(client, admin_headers, "Ours", assigned_to=mine.id)
    hers = await _lead(client, admin_headers, "Hers", assigned_to=theirs.id)
    unclaimed = await _lead(client, admin_headers, "Unclaimed")

    headers = await auth_headers(mine)
    listed = await client.get(LEADS, params={"limit": 100}, headers=headers)
    names = {item["first_name"] for item in listed.json()["items"]}

    assert "Ours" in names
    # The unclaimed queue stays visible — that is how a new enquiry gets picked
    # up, and hiding it would leave it sitting unworked.
    assert "Unclaimed" in names
    assert "Hers" not in names

    # ...and the id in the address bar does not get around it.
    assert (await client.get(f"{LEADS}/{hers['id']}", headers=headers)).status_code == 404
    assert (await client.get(f"{LEADS}/{ours['id']}", headers=headers)).status_code == 200
    assert (await client.get(f"{LEADS}/{unclaimed['id']}", headers=headers)).status_code == 200

    # An admin still sees all three.
    all_names = {i["first_name"] for i in (await client.get(LEADS, params={"limit": 100}, headers=admin_headers)).json()["items"]}
    assert {"Ours", "Hers", "Unclaimed"} <= all_names


async def test_a_counsellor_cannot_write_to_another_counsellors_lead(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """Narrowing the read without narrowing the write would be theatre."""
    admin = await user_factory(UserRole.ADMIN, email="scope.w.admin@example.com")
    mine = await user_factory(UserRole.COUNSELLOR, email="scope.w.mine@example.com")
    theirs = await user_factory(UserRole.COUNSELLOR, email="scope.w.theirs@example.com")

    hers = await _lead(client, await auth_headers(admin), "Hers", assigned_to=theirs.id)
    headers = await auth_headers(mine)

    assert (await client.patch(f"{LEADS}/{hers['id']}", json={"first_name": "Stolen"}, headers=headers)).status_code == 404
    assert (await client.post(f"{LEADS}/{hers['id']}/status", json={"status": "contacted"}, headers=headers)).status_code == 404
    assert (await client.post(f"{LEADS}/{hers['id']}/assign", json={"assigned_to": str(mine.id)}, headers=headers)).status_code == 404


async def test_a_counsellor_can_claim_an_unassigned_lead(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The reason unclaimed rows stay visible at all."""
    admin = await user_factory(UserRole.ADMIN, email="scope.c.admin@example.com")
    counsellor = await user_factory(UserRole.COUNSELLOR, email="scope.c.mine@example.com")

    lead = await _lead(client, await auth_headers(admin), "Walkin")
    headers = await auth_headers(counsellor)

    claimed = await client.post(
        f"{LEADS}/{lead['id']}/assign", json={"assigned_to": str(counsellor.id)}, headers=headers
    )
    assert claimed.status_code == 200
    assert claimed.json()["assigned_to"] == str(counsellor.id)


# ── Applications ──────────────────────────────────────────────────────────────


async def test_a_counsellor_does_not_see_another_counsellors_applications(
    client: AsyncClient, user_factory, auth_headers, program_id
) -> None:
    admin = await user_factory(UserRole.ADMIN, email="scope.a.admin@example.com")
    mine = await user_factory(UserRole.COUNSELLOR, email="scope.a.mine@example.com")
    theirs = await user_factory(UserRole.COUNSELLOR, email="scope.a.theirs@example.com")
    student_a = await user_factory(UserRole.STUDENT, email="scope.a.one@example.com")
    student_b = await user_factory(UserRole.STUDENT, email="scope.a.two@example.com")
    admin_headers = await auth_headers(admin)

    ours = await client.post(
        APPLICATIONS,
        json={"student_id": str(student_a.id), "program_id": program_id, "counsellor_id": str(mine.id)},
        headers=admin_headers,
    )
    hers = await client.post(
        APPLICATIONS,
        json={"student_id": str(student_b.id), "program_id": program_id, "counsellor_id": str(theirs.id)},
        headers=admin_headers,
    )
    assert ours.status_code == 200 and hers.status_code == 200

    headers = await auth_headers(mine)
    ids = {i["id"] for i in (await client.get(APPLICATIONS, params={"limit": 100}, headers=headers)).json()["items"]}
    assert ours.json()["id"] in ids
    assert hers.json()["id"] not in ids

    assert (await client.get(f"{APPLICATIONS}/{hers.json()['id']}", headers=headers)).status_code == 404
    assert (
        await client.patch(f"{APPLICATIONS}/{hers.json()['id']}", json={"remarks": "no"}, headers=headers)
    ).status_code == 404


# ── Messages ──────────────────────────────────────────────────────────────────


async def test_a_counsellor_does_not_read_another_counsellors_student_thread(
    client: AsyncClient, user_factory, auth_headers, program_id
) -> None:
    admin = await user_factory(UserRole.ADMIN, email="scope.m.admin@example.com")
    mine = await user_factory(UserRole.COUNSELLOR, email="scope.m.mine@example.com")
    theirs = await user_factory(UserRole.COUNSELLOR, email="scope.m.theirs@example.com")
    my_student = await user_factory(UserRole.STUDENT, email="scope.m.one@example.com")
    their_student = await user_factory(UserRole.STUDENT, email="scope.m.two@example.com")
    admin_headers = await auth_headers(admin)

    for student, counsellor in ((my_student, mine), (their_student, theirs)):
        created = await client.post(
            APPLICATIONS,
            json={
                "student_id": str(student.id),
                "program_id": program_id,
                "counsellor_id": str(counsellor.id),
            },
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text

    # Each student writes in. A support thread is where students say the things
    # they would not put on a form, so this is the disclosure that matters most.
    for student in (my_student, their_student):
        sent = await client.post(
            "/api/v1/student/me/messages",
            json={"body": f"private note from {student.email}"},
            headers=await auth_headers(student),
        )
        assert sent.status_code in (200, 201), sent.text

    headers = await auth_headers(mine)
    threads = await client.get(f"{MESSAGES}/threads", headers=headers)
    assert threads.status_code == 200
    visible = {t["student_id"] for t in threads.json()}
    assert str(my_student.id) in visible
    assert str(their_student.id) not in visible

    # The by-id read is closed too, and says 404 rather than 403 so the id is
    # not an oracle for which students exist.
    assert (await client.get(f"{MESSAGES}/{their_student.id}", headers=headers)).status_code == 404
    assert (await client.get(f"{MESSAGES}/{my_student.id}", headers=headers)).status_code == 200

    # ...and cannot be written to either.
    assert (
        await client.post(f"{MESSAGES}/{their_student.id}", json={"body": "hello"}, headers=headers)
    ).status_code == 404

    # An admin sees both, which is the point of the exception.
    admin_visible = {t["student_id"] for t in (await client.get(f"{MESSAGES}/threads", headers=admin_headers)).json()}
    assert {str(my_student.id), str(their_student.id)} <= admin_visible


async def test_an_unclaimed_students_first_message_is_visible_to_counsellors(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The half of the rule that stops it causing harm.

    A student writes in before anyone is assigned to them. If "yours" were the
    whole rule that message would be invisible to every counsellor and sit
    unanswered until an admin happened to look.
    """
    counsellor = await user_factory(UserRole.COUNSELLOR, email="scope.u.mine@example.com")
    student = await user_factory(UserRole.STUDENT, email="scope.u.student@example.com")

    sent = await client.post(
        "/api/v1/student/me/messages",
        json={"body": "Hello, is anyone there?"},
        headers=await auth_headers(student),
    )
    assert sent.status_code in (200, 201), sent.text

    headers = await auth_headers(counsellor)
    visible = {t["student_id"] for t in (await client.get(f"{MESSAGES}/threads", headers=headers)).json()}
    assert str(student.id) in visible
    assert (await client.get(f"{MESSAGES}/{student.id}", headers=headers)).status_code == 200


# ── Soft delete ───────────────────────────────────────────────────────────────


async def test_deleting_a_lead_hides_it_without_destroying_it(
    client: AsyncClient, user_factory, auth_headers, session
) -> None:
    """Gone from the product, kept in the database.

    The console now has a delete button, so this has to be recoverable: a hard
    delete cascaded to the lead's activity log and follow-ups — the record of
    what was said and promised, which is what you need precisely when someone
    asks why a lead was dropped.
    """
    from sqlalchemy import select

    from app.models import Lead

    admin = await user_factory(UserRole.ADMIN, email="scope.d.admin@example.com")
    headers = await auth_headers(admin)
    lead = await _lead(client, headers, "Doomed")

    assert (await client.delete(f"{LEADS}/{lead['id']}", headers=headers)).status_code == 200

    # Invisible everywhere the product looks...
    assert (await client.get(f"{LEADS}/{lead['id']}", headers=headers)).status_code == 404
    listed = {i["first_name"] for i in (await client.get(LEADS, params={"limit": 100}, headers=headers)).json()["items"]}
    assert "Doomed" not in listed

    # ...but still on disk, with its history intact.
    session.expire_all()
    row = await session.scalar(select(Lead).where(Lead.id == lead["id"]))
    assert row is not None
    assert row.deleted_at is not None


async def test_deleting_an_application_hides_it_from_staff_and_the_student(
    client: AsyncClient, user_factory, auth_headers, session, program_id
) -> None:
    from sqlalchemy import select

    from app.models import Application

    admin = await user_factory(UserRole.ADMIN, email="scope.d2.admin@example.com")
    student = await user_factory(UserRole.STUDENT, email="scope.d2.student@example.com")
    headers = await auth_headers(admin)

    created = await client.post(
        APPLICATIONS,
        json={"student_id": str(student.id), "program_id": program_id},
        headers=headers,
    )
    application_id = created.json()["id"]

    assert (await client.delete(f"{APPLICATIONS}/{application_id}", headers=headers)).status_code == 200
    assert (await client.get(f"{APPLICATIONS}/{application_id}", headers=headers)).status_code == 404

    # The student's own portal must not keep showing it either.
    student_headers = await auth_headers(student)
    mine = await client.get("/api/v1/applications", headers=student_headers)
    assert application_id not in {i["id"] for i in mine.json()["items"]}

    session.expire_all()
    row = await session.scalar(select(Application).where(Application.id == application_id))
    assert row is not None and row.deleted_at is not None


# ── Application search ────────────────────────────────────────────────────────


async def test_staff_can_search_applications_by_the_things_they_know(
    client: AsyncClient, user_factory, auth_headers, program_id, session
) -> None:
    """The applications list had no search box and no `search` parameter at all.

    Staff know a file by the applicant, the course, the university, or the
    reference they were given on the phone — so all four have to find it.
    """
    admin = await user_factory(UserRole.ADMIN, email="scope.s.admin@example.com")
    wanted = await user_factory(UserRole.STUDENT, email="parbati.thapa@example.com")
    other = await user_factory(UserRole.STUDENT, email="unrelated@example.com")
    # The factory names users after their role, so give these two the names the
    # search is actually meant to find.
    wanted.first_name, wanted.last_name = "Parbati", "Thapa"
    other.first_name, other.last_name = "Unrelated", "Person"
    await session.commit()
    headers = await auth_headers(admin)

    mine = await client.post(
        APPLICATIONS, json={"student_id": str(wanted.id), "program_id": program_id}, headers=headers
    )
    await client.post(
        APPLICATIONS, json={"student_id": str(other.id), "program_id": program_id}, headers=headers
    )
    application_id = mine.json()["id"]

    async def search(term: str) -> set[str]:
        response = await client.get(APPLICATIONS, params={"search": term, "limit": 100}, headers=headers)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert application_id in await search("Parbati")
    assert application_id in await search("thapa")
    assert application_id in await search("parbati.thapa@example.com")
    assert application_id in await search("Marine")          # the course
    assert application_id in await search("Cardiff")         # the university
    assert application_id in await search(application_id[:8])  # the raw id

    # Both applications share a course, so a course search finds two while a
    # name search finds one — proving the filter narrows rather than passing
    # everything through.
    assert len(await search("Marine")) == 2
    assert len(await search("Parbati")) == 1

    # And the reference as the console prints it, IGN-YYYY-NNNNNN, which is
    # derived from the id rather than stored.
    serial = int(application_id.replace("-", "")[:6], 16) % 1_000_000
    assert application_id in await search(f"IGN-2026-{serial:06d}")
    assert application_id in await search(f"{serial:06d}")
