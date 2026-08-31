"""Student and employee profiles, departments, and the people directory.

The student-profile sub-resources are the sharpest edge in this slice: ED360
authorises them against the `user_id` in the path and then looks entries up by
`entry_id` alone, so the two are never checked against each other. With no
tenancy safety net underneath, that has to be closed structurally here.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

USERS = "/api/v1/users"
DEPARTMENTS = "/api/v1/departments"
EMPLOYEES = "/api/v1/employees"


def _profile_url(user_id) -> str:
    return f"{USERS}/{user_id}/student-profile"


async def _make_profile(client: AsyncClient, user, headers: dict[str, str], **overrides) -> dict:
    payload = {"education_level": "bachelor", "nationality": "Nepali"}
    payload.update(overrides)
    response = await client.patch(_profile_url(user.id), json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _add_education(client: AsyncClient, user, headers: dict[str, str], **overrides) -> dict:
    payload = {"institution_name": "Kathmandu University", "degree_level": "bachelor"}
    payload.update(overrides)
    response = await client.post(f"{USERS}/{user.id}/education", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── Student profile ───────────────────────────────────────────────────────────


async def test_a_student_creates_and_reads_their_own_profile(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)

    created = await _make_profile(client, student, headers)
    assert created["education_level"] == "bachelor"

    fetched = await client.get(_profile_url(student.id), headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["nationality"] == "Nepali"


async def test_creating_a_profile_requires_education_level(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    response = await client.patch(
        _profile_url(student.id),
        json={"nationality": "Nepali"},
        headers=await auth_headers(student),
    )
    assert response.status_code == 400


async def test_a_student_cannot_read_another_students_profile(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    owner = await user_factory(UserRole.STUDENT)
    await _make_profile(client, owner, await auth_headers(owner))

    intruder = await user_factory(UserRole.STUDENT)
    response = await client.get(_profile_url(owner.id), headers=await auth_headers(intruder))
    assert response.status_code == 403


async def test_a_counsellor_can_read_a_students_profile(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    await _make_profile(client, student, await auth_headers(student))

    counsellor = await user_factory(UserRole.COUNSELLOR)
    response = await client.get(_profile_url(student.id), headers=await auth_headers(counsellor))
    assert response.status_code == 200


async def test_finance_staff_cannot_read_a_students_profile(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """Being staff is not enough — the profile carries passport and family
    details, so it is limited to student-facing roles."""
    student = await user_factory(UserRole.STUDENT)
    await _make_profile(client, student, await auth_headers(student))

    finance = await user_factory(UserRole.FINANCE)
    response = await client.get(_profile_url(student.id), headers=await auth_headers(finance))
    assert response.status_code == 403


# ── The ownership hole ────────────────────────────────────────────────────────


async def test_a_student_cannot_edit_another_students_education_entry(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """The IDOR: pair your own `user_id` (which passes the ownership check)
    with a stranger's `entry_id`."""
    victim = await user_factory(UserRole.STUDENT)
    victim_headers = await auth_headers(victim)
    await _make_profile(client, victim, victim_headers)
    victim_entry = await _add_education(client, victim, victim_headers)

    attacker = await user_factory(UserRole.STUDENT)
    attacker_headers = await auth_headers(attacker)
    await _make_profile(client, attacker, attacker_headers)

    tampered = await client.patch(
        f"{USERS}/{attacker.id}/education/{victim_entry['id']}",
        json={"institution_name": "Tampered"},
        headers=attacker_headers,
    )
    assert tampered.status_code == 404

    still_intact = await client.get(f"{USERS}/{victim.id}/education", headers=victim_headers)
    assert still_intact.json()[0]["institution_name"] == "Kathmandu University"


async def test_a_student_cannot_delete_another_students_education_entry(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    victim = await user_factory(UserRole.STUDENT)
    victim_headers = await auth_headers(victim)
    await _make_profile(client, victim, victim_headers)
    victim_entry = await _add_education(client, victim, victim_headers)

    attacker = await user_factory(UserRole.STUDENT)
    attacker_headers = await auth_headers(attacker)
    await _make_profile(client, attacker, attacker_headers)

    deleted = await client.delete(
        f"{USERS}/{attacker.id}/education/{victim_entry['id']}",
        headers=attacker_headers,
    )
    assert deleted.status_code == 404
    assert len((await client.get(f"{USERS}/{victim.id}/education", headers=victim_headers)).json()) == 1


async def test_a_student_cannot_touch_another_students_experience_entry(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    victim = await user_factory(UserRole.STUDENT)
    victim_headers = await auth_headers(victim)
    await _make_profile(client, victim, victim_headers)
    entry = await client.post(
        f"{USERS}/{victim.id}/experience",
        json={"company_name": "Acme", "job_title": "Intern"},
        headers=victim_headers,
    )
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()["id"]

    attacker = await user_factory(UserRole.STUDENT)
    attacker_headers = await auth_headers(attacker)
    await _make_profile(client, attacker, attacker_headers)

    assert (
        await client.patch(
            f"{USERS}/{attacker.id}/experience/{entry_id}",
            json={"company_name": "Tampered", "job_title": "Intern"},
            headers=attacker_headers,
        )
    ).status_code == 404
    assert (
        await client.delete(f"{USERS}/{attacker.id}/experience/{entry_id}", headers=attacker_headers)
    ).status_code == 404


async def test_a_student_manages_their_own_education_entries(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)
    await _make_profile(client, student, headers)
    entry = await _add_education(client, student, headers)

    updated = await client.patch(
        f"{USERS}/{student.id}/education/{entry['id']}",
        json={"institution_name": "Tribhuvan University"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["institution_name"] == "Tribhuvan University"

    assert (await client.delete(f"{USERS}/{student.id}/education/{entry['id']}", headers=headers)).status_code == 200
    assert (await client.get(f"{USERS}/{student.id}/education", headers=headers)).json() == []


# ── Employee profile ──────────────────────────────────────────────────────────


async def test_admin_upserts_an_employee_profile_and_records_a_timeline(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    staff = await user_factory(UserRole.COUNSELLOR)
    url = f"{USERS}/{staff.id}/employee-profile"

    created = await client.patch(url, json={"designation": "Counsellor", "employee_code": "E001"}, headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["designation"] == "Counsellor"

    promoted = await client.patch(url, json={"designation": "Senior Counsellor"}, headers=headers)
    assert promoted.status_code == 200

    timeline = await client.get(f"{url}/timeline", headers=headers)
    assert timeline.status_code == 200
    events = timeline.json()
    # Creation, then the designation change.
    assert {event["event_type"] for event in events} == {"joined", "designation_changed"}
    change = next(event for event in events if event["event_type"] == "designation_changed")
    assert change["previous_value"] == "Counsellor"
    assert change["new_value"] == "Senior Counsellor"


async def test_staff_read_their_own_employee_profile_but_cannot_edit_it(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    staff = await user_factory(UserRole.FINANCE)
    url = f"{USERS}/{staff.id}/employee-profile"

    await client.patch(url, json={"designation": "Accountant"}, headers=await auth_headers(admin))

    staff_headers = await auth_headers(staff)
    assert (await client.get(url, headers=staff_headers)).status_code == 200
    # Designation is not a self-service field.
    assert (await client.patch(url, json={"designation": "Head of Finance"}, headers=staff_headers)).status_code == 403


async def test_staff_cannot_read_a_colleagues_employee_profile(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    subject = await user_factory(UserRole.COUNSELLOR)
    await client.patch(
        f"{USERS}/{subject.id}/employee-profile",
        json={"designation": "Counsellor"},
        headers=await auth_headers(admin),
    )

    colleague = await user_factory(UserRole.FINANCE)
    response = await client.get(
        f"{USERS}/{subject.id}/employee-profile",
        headers=await auth_headers(colleague),
    )
    assert response.status_code == 403


# ── Departments ───────────────────────────────────────────────────────────────


async def test_department_crud_and_employee_count(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    created = await client.post(DEPARTMENTS, json={"name": "Admissions"}, headers=headers)
    assert created.status_code == 200, created.text
    department_id = created.json()["id"]
    assert created.json()["employee_count"] == 0

    staff = await user_factory(UserRole.ADMISSIONS)
    await client.patch(
        f"{USERS}/{staff.id}/employee-profile",
        json={"department_id": department_id},
        headers=headers,
    )

    fetched = await client.get(f"{DEPARTMENTS}/{department_id}", headers=headers)
    assert fetched.json()["employee_count"] == 1

    listed = await client.get(DEPARTMENTS, headers=headers)
    assert listed.json()["items"][0]["employee_count"] == 1


async def test_a_populated_department_cannot_be_deleted(client: AsyncClient, user_factory, auth_headers) -> None:
    """`department_id` is ON DELETE SET NULL, so an unchecked delete would
    silently orphan staff instead of failing."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    department_id = (await client.post(DEPARTMENTS, json={"name": "Admissions"}, headers=headers)).json()["id"]

    staff = await user_factory(UserRole.ADMISSIONS)
    await client.patch(
        f"{USERS}/{staff.id}/employee-profile",
        json={"department_id": department_id},
        headers=headers,
    )

    blocked = await client.delete(f"{DEPARTMENTS}/{department_id}", headers=headers)
    assert blocked.status_code == 400
    assert "Reassign" in blocked.json()["detail"]

    # Empty it, and the delete goes through. This is the step ED360 cannot
    # perform at all: its upsert skips nulls, so a staff member can be moved to
    # another department but never out of one, leaving the "reassign first"
    # instruction impossible to follow for the last department.
    reassigned = await client.patch(
        f"{USERS}/{staff.id}/employee-profile",
        json={"department_id": None},
        headers=headers,
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["department_id"] is None

    assert (await client.delete(f"{DEPARTMENTS}/{department_id}", headers=headers)).status_code == 200


async def test_clearing_a_department_is_recorded_on_the_timeline(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    department_id = (await client.post(DEPARTMENTS, json={"name": "Admissions"}, headers=headers)).json()["id"]
    staff = await user_factory(UserRole.ADMISSIONS)
    url = f"{USERS}/{staff.id}/employee-profile"

    await client.patch(url, json={"department_id": department_id}, headers=headers)
    await client.patch(url, json={"department_id": None}, headers=headers)

    events = (await client.get(f"{url}/timeline", headers=headers)).json()
    departures = [
        event for event in events if event["event_type"] == "department_changed" and event["new_value"] is None
    ]
    assert len(departures) == 1
    assert departures[0]["previous_value"] == department_id


async def test_a_required_field_cannot_be_nulled_on_a_profile(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)
    await _make_profile(client, student, headers)

    response = await client.patch(_profile_url(student.id), json={"education_level": None}, headers=headers)
    assert response.status_code == 400


async def test_students_cannot_see_departments(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    assert (await client.get(DEPARTMENTS, headers=await auth_headers(student))).status_code == 403


# ── People directory ──────────────────────────────────────────────────────────


async def test_the_directory_includes_staff_without_a_profile(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """The outer join is the point: a staff account with no employee profile
    yet must still be listed, not hidden."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    unfilled = await user_factory(UserRole.SUPPORT)
    await user_factory(UserRole.STUDENT)

    response = await client.get(EMPLOYEES, headers=headers)
    assert response.status_code == 200
    rows = {row["email"]: row for row in response.json()["items"]}

    assert unfilled.email in rows
    assert rows[unfilled.email]["designation"] is None
    # Students are never in the people directory.
    assert all(row["role"] != UserRole.STUDENT.value for row in response.json()["items"])


async def test_the_directory_resolves_department_names(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    department_id = (await client.post(DEPARTMENTS, json={"name": "Admissions"}, headers=headers)).json()["id"]

    staff = await user_factory(UserRole.ADMISSIONS)
    await client.patch(
        f"{USERS}/{staff.id}/employee-profile",
        json={"department_id": department_id, "designation": "Officer"},
        headers=headers,
    )

    response = await client.get(EMPLOYEES, params={"department_id": department_id}, headers=headers)
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["department_name"] == "Admissions"


async def test_ordinary_staff_cannot_browse_the_directory(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    assert (await client.get(EMPLOYEES, headers=await auth_headers(counsellor))).status_code == 403


# ── The research shortlist (CATALOGUE-CMS-PLAN.md Phase 7) ───────────────────

RESEARCH = "/api/v1/users/{user_id}/research"


async def _country(client: AsyncClient, headers: dict[str, str]) -> str:
    """The UK, created once. Countries are unique by name and several of these
    tests need two universities in the same one."""
    existing = await client.get("/api/v1/countries", params={"search": "United Kingdom"}, headers=headers)
    assert existing.status_code == 200, existing.text
    for row in existing.json()["items"]:
        if row["name"] == "United Kingdom":
            return row["id"]

    created = await client.post(
        "/api/v1/countries", json={"name": "United Kingdom", "iso2": "GB"}, headers=headers
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


async def _catalogue_university(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    """A published university with a slug, as the public site would link to."""
    payload = {
        "country_id": await _country(client, headers),
        "name": "University of Essex",
        "slug": "essex",
        "city": "Colchester",
        "region": "England — South",
        "tagline": "A university in Colchester",
        "overview": "A campus university.",
        "is_published": True,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/universities", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _with_research(client: AsyncClient, user, headers: dict[str, str], research: dict) -> None:
    response = await client.patch(
        _profile_url(user.id),
        json={"education_level": "bachelor", "preferences": {"research": research}},
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def test_a_live_shortlist_resolves_to_catalogue_rows(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The whole point of Phase 7: the slug a student shortlisted on the public
    site is `universities.slug`, so a counsellor gets a row rather than a
    string."""
    admin = await user_factory(UserRole.ADMIN)
    admin_headers = await auth_headers(admin)
    university = await _catalogue_university(client, admin_headers)

    student = await user_factory(UserRole.STUDENT)
    await _with_research(
        client,
        student,
        await auth_headers(student),
        {"catalogue": "live", "compared": ["essex"], "universities": []},
    )

    response = await client.get(RESEARCH.format(user_id=student.id), headers=admin_headers)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["catalogue"] == "live"
    assert [entry["slug"] for entry in body["universities"]] == ["essex"]
    assert body["universities"][0]["id"] == university["id"]
    assert body["universities"][0]["name"] == "University of Essex"
    assert body["unresolved"] == []


async def test_a_pre_import_shortlist_is_never_resolved(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """A `v1` handoff names institutions that never existed. If one of its
    slugs happens to collide with a real one, resolving it would attach a
    student to a university they never looked at."""
    admin = await user_factory(UserRole.ADMIN)
    admin_headers = await auth_headers(admin)
    await _catalogue_university(client, admin_headers)

    student = await user_factory(UserRole.STUDENT)
    await _with_research(
        client,
        student,
        await auth_headers(student),
        {"catalogue": "example", "compared": ["essex"], "universities": []},
    )

    body = (await client.get(RESEARCH.format(user_id=student.id), headers=admin_headers)).json()
    assert body["catalogue"] == "example"
    assert body["universities"] == []
    assert body["unresolved"] == ["essex"]


async def test_research_without_a_catalogue_stamp_is_treated_as_pre_import(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """Records written before the stamp existed all predate the import."""
    admin_headers = await auth_headers(await user_factory(UserRole.ADMIN))
    student = await user_factory(UserRole.STUDENT)
    await _with_research(
        client, student, await auth_headers(student), {"compared": ["essex"], "universities": []}
    )

    body = (await client.get(RESEARCH.format(user_id=student.id), headers=admin_headers)).json()
    assert body["catalogue"] == "example"
    assert body["universities"] == []


async def test_a_withdrawn_shortlist_entry_is_reported_not_dropped(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """"They shortlisted something we no longer list" is information, and a
    silently shorter list is not."""
    admin_headers = await auth_headers(await user_factory(UserRole.ADMIN))
    await _catalogue_university(client, admin_headers)

    student = await user_factory(UserRole.STUDENT)
    await _with_research(
        client,
        student,
        await auth_headers(student),
        {"catalogue": "live", "compared": ["essex", "gone-away"], "universities": []},
    )

    body = (await client.get(RESEARCH.format(user_id=student.id), headers=admin_headers)).json()
    assert [entry["slug"] for entry in body["universities"]] == ["essex"]
    assert body["unresolved"] == ["gone-away"]


async def test_the_shortlist_keeps_the_students_own_order(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The first thing they compared is not the same as the last."""
    admin_headers = await auth_headers(await user_factory(UserRole.ADMIN))

    await _catalogue_university(client, admin_headers)
    second = await _catalogue_university(
        client,
        admin_headers,
        name="Aston University",
        slug="aston",
        city="Birmingham",
        region="England — Midlands",
        tagline="A university in Birmingham",
        overview="A city university.",
    )
    assert second["slug"] == "aston"

    student = await user_factory(UserRole.STUDENT)
    await _with_research(
        client,
        student,
        await auth_headers(student),
        {"catalogue": "live", "compared": ["aston", "essex"], "universities": []},
    )

    body = (await client.get(RESEARCH.format(user_id=student.id), headers=admin_headers)).json()
    assert [entry["slug"] for entry in body["universities"]] == ["aston", "essex"]


async def test_a_student_with_no_profile_has_an_empty_shortlist(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """Not a 404: a student who has not been through onboarding has no
    research, which is an empty shortlist rather than a missing resource."""
    admin_headers = await auth_headers(await user_factory(UserRole.ADMIN))
    student = await user_factory(UserRole.STUDENT)

    response = await client.get(RESEARCH.format(user_id=student.id), headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"catalogue": "none", "universities": [], "unresolved": []}


async def test_a_student_cannot_read_another_students_shortlist(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    owner = await user_factory(UserRole.STUDENT)
    intruder = await user_factory(UserRole.STUDENT)

    response = await client.get(
        RESEARCH.format(user_id=owner.id), headers=await auth_headers(intruder)
    )
    assert response.status_code == 403
