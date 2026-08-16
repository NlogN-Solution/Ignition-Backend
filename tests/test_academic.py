"""Catalog endpoints (countries, universities, programs, intakes).

Bucket B: the four catalog tables were already tenant-free in ED360, so this is
the one part of the port with nothing to strip. The tests therefore concentrate
on the access split — any authenticated user reads, only admins write — and on
the two places this port deliberately diverges.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

COUNTRIES = "/api/v1/countries"
UNIVERSITIES = "/api/v1/universities"
PROGRAMS = "/api/v1/programs"
INTAKES = "/api/v1/intakes"


async def _make_country(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    payload = {"name": "Australia", "iso2": "AU", "iso3": "AUS", "phone_code": "+61"}
    payload.update(overrides)
    response = await client.post(COUNTRIES, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _make_university(client: AsyncClient, headers: dict[str, str], country_id: str, **overrides) -> dict:
    payload = {"country_id": country_id, "name": "University of Melbourne", "city": "Melbourne"}
    payload.update(overrides)
    response = await client.post(UNIVERSITIES, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _make_program(client: AsyncClient, headers: dict[str, str], university_id: str, **overrides) -> dict:
    payload = {"university_id": university_id, "name": "MSc Computer Science", "degree_level": "master"}
    payload.update(overrides)
    response = await client.post(PROGRAMS, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── Access split ──────────────────────────────────────────────────────────────


async def test_students_can_read_the_catalog(client: AsyncClient, user_factory, auth_headers) -> None:
    """Not an oversight: the student portal's whole job is browsing this."""
    admin = await user_factory(UserRole.ADMIN)
    await _make_country(client, await auth_headers(admin))

    student = await user_factory(UserRole.STUDENT)
    response = await client.get(COUNTRIES, headers=await auth_headers(student))

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_students_cannot_write_to_the_catalog(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    response = await client.post(
        COUNTRIES,
        json={"name": "Nepal", "iso2": "NP"},
        headers=await auth_headers(student),
    )
    assert response.status_code == 403


async def test_counsellors_cannot_write_to_the_catalog(client: AsyncClient, user_factory, auth_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    response = await client.post(
        COUNTRIES,
        json={"name": "Nepal", "iso2": "NP"},
        headers=await auth_headers(counsellor),
    )
    assert response.status_code == 403


async def test_the_catalog_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(COUNTRIES)).status_code == 401


# ── CRUD round trip ───────────────────────────────────────────────────────────


async def test_country_crud_round_trip(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    country = await _make_country(client, headers)
    country_id = country["id"]

    fetched = await client.get(f"{COUNTRIES}/{country_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["iso2"] == "AU"

    updated = await client.patch(f"{COUNTRIES}/{country_id}", json={"ranking": None, "name": "Aus"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Aus"

    assert (await client.delete(f"{COUNTRIES}/{country_id}", headers=headers)).status_code == 200
    assert (await client.get(f"{COUNTRIES}/{country_id}", headers=headers)).status_code == 404


async def test_missing_entities_are_404(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    absent = "00000000-0000-0000-0000-000000000000"

    for base in (COUNTRIES, UNIVERSITIES, PROGRAMS, INTAKES):
        assert (await client.get(f"{base}/{absent}", headers=headers)).status_code == 404
        assert (await client.delete(f"{base}/{absent}", headers=headers)).status_code == 404


# ── PATCH null semantics (deliberate divergence from ED360) ───────────────────


async def test_patch_null_clears_a_nullable_field(client: AsyncClient, user_factory, auth_headers) -> None:
    """ED360's services skip `None` on update, so clearing an optional field is
    impossible — `{"website": null}` silently does nothing."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    country = await _make_country(client, headers)
    university = await _make_university(client, headers, country["id"], website="https://old.example.com")
    assert university["website"] == "https://old.example.com"

    cleared = await client.patch(
        f"{UNIVERSITIES}/{university['id']}",
        json={"website": None},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["website"] is None


async def test_patch_null_on_a_required_field_is_400_not_500(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _make_country(client, headers)

    response = await client.patch(f"{COUNTRIES}/{country['id']}", json={"name": None}, headers=headers)
    assert response.status_code == 400
    assert "name" in response.json()["detail"]


async def test_omitted_fields_are_left_alone(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _make_country(client, headers, phone_code="+61")

    updated = await client.patch(f"{COUNTRIES}/{country['id']}", json={"name": "Oz"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["phone_code"] == "+61"


# ── Filtering ─────────────────────────────────────────────────────────────────


async def test_universities_filter_by_country_and_partner(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    au = await _make_country(client, headers)
    nz = await _make_country(client, headers, name="New Zealand", iso2="NZ", iso3="NZL")
    await _make_university(client, headers, au["id"], name="Melbourne", is_partner=True)
    await _make_university(client, headers, au["id"], name="Monash", is_partner=False)
    await _make_university(client, headers, nz["id"], name="Auckland")

    by_country = await client.get(UNIVERSITIES, params={"country_id": au["id"]}, headers=headers)
    assert by_country.json()["total"] == 2

    partners = await client.get(
        UNIVERSITIES,
        params={"country_id": au["id"], "is_partner": "true"},
        headers=headers,
    )
    assert [item["name"] for item in partners.json()["items"]] == ["Melbourne"]


async def test_search_is_case_insensitive(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    await _make_country(client, headers)

    response = await client.get(COUNTRIES, params={"search": "AUSTRAL"}, headers=headers)
    assert response.json()["total"] == 1


async def test_programs_and_intakes_nest_under_their_parents(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    country = await _make_country(client, headers)
    university = await _make_university(client, headers, country["id"])
    program = await _make_program(client, headers, university["id"])

    intake = await client.post(
        INTAKES,
        json={"program_id": program["id"], "name": "Feb 2027", "start_date": "2027-02-01"},
        headers=headers,
    )
    assert intake.status_code == 200, intake.text

    by_university = await client.get(PROGRAMS, params={"university_id": university["id"]}, headers=headers)
    assert by_university.json()["total"] == 1

    by_program = await client.get(INTAKES, params={"program_id": program["id"]}, headers=headers)
    assert by_program.json()["total"] == 1
    assert by_program.json()["items"][0]["name"] == "Feb 2027"


async def test_deleting_a_program_cascades_to_its_intakes(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    country = await _make_country(client, headers)
    university = await _make_university(client, headers, country["id"])
    program = await _make_program(client, headers, university["id"])
    await client.post(INTAKES, json={"program_id": program["id"], "name": "Feb 2027"}, headers=headers)

    assert (await client.delete(f"{PROGRAMS}/{program['id']}", headers=headers)).status_code == 200
    assert (await client.get(INTAKES, params={"program_id": program["id"]}, headers=headers)).json()["total"] == 0


# ── Phase 4 catalog enrichment ────────────────────────────────────────────────


async def test_enriched_catalog_fields_round_trip(client: AsyncClient, user_factory, auth_headers) -> None:
    """The Phase 4 columns must reach the API, not just the database — the exit
    criterion is that catalog endpoints serve what the student portal needs."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    country = await _make_country(
        client,
        headers,
        average_tuition_usd=32000.0,
        average_living_cost_usd=15000.0,
        visa_information="Subclass 500 student visa.",
        display_order=1,
    )
    assert country["average_tuition_usd"] == 32000.0
    assert country["visa_information"].startswith("Subclass 500")
    assert country["display_order"] == 1

    university = await _make_university(
        client,
        headers,
        country["id"],
        acceptance_rate=70.5,
        faculties=["Engineering", "Business"],
        highlights=["Group of Eight"],
        campus_type="urban",
    )
    assert university["faculties"] == ["Engineering", "Business"]
    assert university["acceptance_rate"] == 70.5
    assert university["campus_type"] == "urban"

    program = await _make_program(
        client,
        headers,
        university["id"],
        intakes_summary=["Feb", "Jul"],
        outcomes=["Data Scientist"],
        # Objects, matching the source data: requirements keyed by section,
        # key dates by milestone.
        requirements={"academic": ["Bachelor in CS"], "documents": ["Transcripts"]},
        key_dates={"applicationDeadline": "2027-01-15"},
        course_type="coursework",
    )
    assert program["intakes_summary"] == ["Feb", "Jul"]
    assert program["key_dates"]["applicationDeadline"] == "2027-01-15"
    assert program["requirements"]["academic"] == ["Bachelor in CS"]
    assert program["course_type"] == "coursework"

    # And they survive a re-read, not just the create response.
    fetched = await client.get(f"{PROGRAMS}/{program['id']}", headers=headers)
    assert fetched.json()["outcomes"] == ["Data Scientist"]
