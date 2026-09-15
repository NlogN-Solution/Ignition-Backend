"""Apply Now, across the authentication boundary.

The feature these cover in one sentence: a visitor who presses Apply Now on a
course and then makes an account must not be asked which course they meant.

The interesting cases are not the happy path but the four ways the context used
to be lost — the student registered, the student logged in instead, the student
came back the next day, or somebody forwarded the link.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

PUBLIC = "/api/v1/public"
STUDENT = "/api/v1/student"
COUNTRIES = "/api/v1/countries"
UNIVERSITIES = "/api/v1/universities"
PROGRAMS = "/api/v1/programs"


async def _catalogue(client: AsyncClient, headers: dict[str, str]) -> dict:
    """One published offering, reachable on its public slug."""
    country = (await client.post(COUNTRIES, json={"name": "United Kingdom", "iso2": "GB"}, headers=headers)).json()
    university = (
        await client.post(
            UNIVERSITIES,
            json={
                "country_id": country["id"],
                "name": "University of Coventry",
                "slug": "coventry",
                "city": "Coventry",
                # `region` and `tagline` are in AcademicService.PUBLISH_REQUIRED
                # — the public site reads them unconditionally, so publishing
                # without them is a runtime hole and the API refuses.
                "region": "England — Midlands",
                "tagline": "A modern university in the Midlands",
                "overview": "Coventry has taught since 1843.",
                "is_published": True,
            },
            headers=headers,
        )
    ).json()
    program = (
        await client.post(
            PROGRAMS,
            json={
                "university_id": university["id"],
                "name": "MSc Computer Science",
                "slug": "msc-computer-science-coventry",
                "subject": "Computing",
                "course_level": "Postgraduate",
                "is_published": True,
            },
            headers=headers,
        )
    ).json()
    return {"university": university, "program": program}


async def _mint(client: AsyncClient, slug: str = "msc-computer-science-coventry") -> dict:
    response = await client.post(
        f"{PUBLIC}/apply-intents",
        json={"course_slug": slug, "source_path": "/courses/msc-computer-science-coventry"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ── Minting ──────────────────────────────────────────────────────────────────


async def test_pressing_apply_records_the_resolved_course(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The slug is a lookup key, not data.

    Everything in the response comes back out of the database — which is what
    makes it safe to render on a page the visitor has not authenticated for.
    """
    admin = await user_factory(UserRole.ADMIN)
    catalogue = await _catalogue(client, await auth_headers(admin))

    intent = await _mint(client)

    assert intent["course"]["course_name"] == "MSc Computer Science"
    assert intent["course"]["university_name"] == "University of Coventry"
    assert intent["course"]["program_id"] == catalogue["program"]["id"]
    assert intent["claimed_at"] is None
    assert intent["is_fulfilled"] is False


async def test_an_unknown_course_mints_nothing(client: AsyncClient) -> None:
    response = await client.post(f"{PUBLIC}/apply-intents", json={"course_slug": "does-not-exist"})
    assert response.status_code == 404


async def test_the_registration_screen_can_read_it_without_a_token(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """"You're applying for X" has to render before the account exists."""
    admin = await user_factory(UserRole.ADMIN)
    await _catalogue(client, await auth_headers(admin))
    intent = await _mint(client)

    response = await client.get(f"{PUBLIC}/apply-intents/{intent['id']}")
    assert response.status_code == 200
    assert response.json()["course"]["course_name"] == "MSc Computer Science"


# ── TEST A / TEST C — surviving registration, and surviving login instead ────


async def test_a_new_student_claims_the_intent_and_onboarding_already_knows(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """TEST A. Register, claim, and the course is waiting."""
    admin = await user_factory(UserRole.ADMIN)
    await _catalogue(client, await auth_headers(admin))
    intent = await _mint(client)

    student = await user_factory(UserRole.STUDENT, email="intent.new@example.com")
    headers = await auth_headers(student)

    claimed = await client.post(f"{STUDENT}/me/apply-intent/{intent['id']}/claim", headers=headers)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["claimed_at"] is not None

    pending = await client.get(f"{STUDENT}/me/apply-intent", headers=headers)
    assert pending.status_code == 200
    assert pending.json()["course"]["course_name"] == "MSc Computer Science"


async def test_the_intent_survives_choosing_login_instead_of_register(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """TEST C. The claim is the same call on both paths.

    Nothing about minting or claiming knows or cares whether the student
    registered or signed in — which is precisely why pressing "Already have an
    account?" cannot lose the course. An existing account claims an intent
    exactly as a brand-new one does.
    """
    admin = await user_factory(UserRole.ADMIN)
    await _catalogue(client, await auth_headers(admin))
    intent = await _mint(client)

    returning = await user_factory(UserRole.STUDENT, email="intent.returning@example.com")
    headers = await auth_headers(returning)

    claimed = await client.post(f"{STUDENT}/me/apply-intent/{intent['id']}/claim", headers=headers)
    assert claimed.status_code == 200, claimed.text
    assert (await client.get(f"{STUDENT}/me/apply-intent", headers=headers)).json()["id"] == intent["id"]


async def test_claiming_twice_is_not_an_error(client: AsyncClient, user_factory, auth_headers) -> None:
    """The portal re-renders; the claim must be idempotent for its owner."""
    admin = await user_factory(UserRole.ADMIN)
    await _catalogue(client, await auth_headers(admin))
    intent = await _mint(client)
    student = await user_factory(UserRole.STUDENT, email="intent.twice@example.com")
    headers = await auth_headers(student)

    first = await client.post(f"{STUDENT}/me/apply-intent/{intent['id']}/claim", headers=headers)
    second = await client.post(f"{STUDENT}/me/apply-intent/{intent['id']}/claim", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["claimed_at"] == second.json()["claimed_at"], "the first claim stands"


async def test_a_forwarded_link_cannot_steal_someone_elses_intent(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """A claimed intent belongs to whoever claimed it, permanently."""
    admin = await user_factory(UserRole.ADMIN)
    await _catalogue(client, await auth_headers(admin))
    intent = await _mint(client)

    owner = await user_factory(UserRole.STUDENT, email="intent.owner@example.com")
    stranger = await user_factory(UserRole.STUDENT, email="intent.stranger@example.com")
    await client.post(f"{STUDENT}/me/apply-intent/{intent['id']}/claim", headers=await auth_headers(owner))

    hijack = await client.post(
        f"{STUDENT}/me/apply-intent/{intent['id']}/claim", headers=await auth_headers(stranger)
    )
    assert hijack.status_code == 404
    assert (await client.get(f"{STUDENT}/me/apply-intent", headers=await auth_headers(stranger))).json() is None


# ── TEST B — the intent stops following them once they have acted ────────────


async def test_opening_the_application_fulfils_the_intent(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """TEST B. Once the application exists, onboarding stops offering the course.

    Fulfilment is keyed on (student, programme) rather than on the intent id, so
    it closes however the application was opened — the apply flow, Explore, or a
    counsellor doing it for them.
    """
    admin = await user_factory(UserRole.ADMIN)
    catalogue = await _catalogue(client, await auth_headers(admin))
    intent = await _mint(client)

    student = await user_factory(UserRole.STUDENT, email="intent.applies@example.com")
    headers = await auth_headers(student)
    await client.post(f"{STUDENT}/me/apply-intent/{intent['id']}/claim", headers=headers)

    created = await client.post(
        f"{STUDENT}/me/applications",
        json={"program_id": catalogue["program"]["id"]},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    assert (await client.get(f"{STUDENT}/me/apply-intent", headers=headers)).json() is None, (
        "a fulfilled intent must stop being offered as 'your selected course'"
    )
