"""Public catalogue and CMS endpoints (CATALOGUE-CMS-PLAN.md Phase 2).

These cover the schema and the admin CRUD surface. The *public* surface — the
unauthenticated, published-only routes the landing site consumes — is Phase 3
and is tested separately; what matters here is that the staff-facing side
exists, is guarded, and round-trips.

Modelled on `test_academic.py`: the access split, the PATCH-null semantics this
port deliberately diverges on, and the two properties specific to this feature —
that `is_published` and `is_active` are genuinely different flags, and that
nothing in the source file is dropped on the way in.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

COUNTRIES = "/api/v1/countries"
UNIVERSITIES = "/api/v1/universities"
PROGRAMS = "/api/v1/programs"
ROUTES = "/api/v1/university-routes"
PROFILES = "/api/v1/course-profiles"
SCHOLARSHIPS = "/api/v1/scholarships"
PAGES = "/api/v1/content-pages"
BLOCKS = "/api/v1/content-blocks"
BLOG = "/api/v1/blog-posts"
MEDIA = "/api/v1/media-assets"


async def _country(client: AsyncClient, headers: dict[str, str]) -> dict:
    response = await client.post(COUNTRIES, json={"name": "United Kingdom", "iso2": "GB"}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _university(client: AsyncClient, headers: dict[str, str], country_id: str, **overrides) -> dict:
    payload = {
        "country_id": country_id,
        "name": "York St John University",
        "slug": "york-st-john",
        "city": "York",
        "region": "England — North",
        "tagline": "A university in York",
    }
    payload.update(overrides)
    response = await client.post(UNIVERSITIES, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── Access split ──────────────────────────────────────────────────────────────


async def test_marketing_can_edit_the_website(client: AsyncClient, user_factory, auth_headers) -> None:
    """`MODULE_ROLES` in the admin console mirrors this; keep the two truthful."""
    admin = await user_factory(UserRole.ADMIN)
    country = await _country(client, await auth_headers(admin))

    marketing = await user_factory(UserRole.MARKETING)
    response = await client.post(
        UNIVERSITIES,
        json={"country_id": country["id"], "name": "Marketing Made This", "slug": "marketing-made-this"},
        headers=await auth_headers(marketing),
    )
    assert response.status_code == 200, response.text


async def test_counsellors_cannot_edit_the_website(client: AsyncClient, user_factory, auth_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    response = await client.post(
        SCHOLARSHIPS,
        json={"slug": "nope", "name": "Nope"},
        headers=await auth_headers(counsellor),
    )
    assert response.status_code == 403


async def test_anonymous_callers_get_nothing(client: AsyncClient) -> None:
    """The unauthenticated surface is Phase 3's `/public/*`, not this router."""
    for path in (ROUTES, PROFILES, SCHOLARSHIPS, PAGES, BLOCKS, BLOG, MEDIA):
        response = await client.get(path)
        assert response.status_code in {401, 403}, f"{path} answered {response.status_code}"


# ── University: the public catalogue columns ─────────────────────────────────


async def test_university_round_trips_every_public_field(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)

    created = await _university(
        client,
        headers,
        country["id"],
        rankings=[{"title": "University of the Year", "source": "The Times", "year": 2026}],
        employability={"employedRate": "94%", "employers": [{"name": "NHS"}], "services": ["Careers desk"]},
        milestones=[{"year": "1841", "label": "Founded"}],
        facilities=["Library", "Studio"],
        accommodation={"guaranteed": True, "weeklyFrom": 120},
        tuition_min=12100,
        tuition_max=14900,
        flyer_url="https://drive.google.com/file/d/abc/view",
    )

    assert created["slug"] == "york-st-john"
    assert created["region"] == "England — North"
    assert created["rankings"][0]["source"] == "The Times"
    assert created["employability"]["employers"][0]["name"] == "NHS"
    assert created["accommodation"]["guaranteed"] is True
    assert created["tuition_min"] == 12100


async def test_is_published_and_is_active_are_different_flags(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """A half-written marketing record must not reach a counsellor's dropdown,
    and an inactive partner must not vanish from a public page mid-cycle."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)

    university = await _university(client, headers, country["id"], is_active=True, is_published=False)
    assert university["is_active"] is True
    assert university["is_published"] is False

    response = await client.get(f"{UNIVERSITIES}?is_active=true", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_slug_is_unique(client: AsyncClient, user_factory, auth_headers) -> None:
    """Slugs are how the public site and the handoff address a university."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    await _university(client, headers, country["id"])

    with pytest.raises(IntegrityError):
        await _university(client, headers, country["id"], name="A Different Name")


# ── Entry routes: the criteria matrix ────────────────────────────────────────


async def test_entry_route_keeps_its_label_and_extras(client: AsyncClient, user_factory, auth_headers) -> None:
    """44 institutions spell the same route ten ways, and unrecognised criteria
    labels must not be silently dropped."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    university = await _university(client, headers, country["id"])

    response = await client.post(
        ROUTES,
        json={
            "university_id": university["id"],
            "route_key": "nursing",
            "label": "BNurs(Adult Nursing)",
            "academic_criteria": "Tribhuvan University 45% OR 2:2 OR Second Division",
            "fee_structure": "LOWER TIER COURSES: £12,100",
            "extras": {"SCOTLAND CAMPUS": "Criteria differ at the Scotland campus"},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    route = response.json()
    assert route["label"] == "BNurs(Adult Nursing)"
    assert route["extras"]["SCOTLAND CAMPUS"].startswith("Criteria differ")


async def test_one_route_per_university_route_and_country(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    university = await _university(client, headers, country["id"])

    payload = {"university_id": university["id"], "route_key": "postgraduate"}
    first = await client.post(ROUTES, json=payload, headers=headers)
    assert first.status_code == 200

    with pytest.raises(IntegrityError):
        await client.post(ROUTES, json=payload, headers=headers)


async def test_deleting_a_university_deletes_its_routes(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    university = await _university(client, headers, country["id"])
    await client.post(
        ROUTES, json={"university_id": university["id"], "route_key": "undergraduate"}, headers=headers
    )

    deleted = await client.delete(f"{UNIVERSITIES}/{university['id']}", headers=headers)
    assert deleted.status_code == 200

    remaining = await client.get(ROUTES, headers=headers)
    assert remaining.json()["total"] == 0


# ── Programs: offerings ──────────────────────────────────────────────────────


async def test_program_carries_the_public_course_fields(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    university = await _university(client, headers, country["id"])

    response = await client.post(
        PROGRAMS,
        json={
            "university_id": university["id"],
            "name": "BA (Hons) Acting",
            "slug": "york-st-john-ba-hons-acting",
            "subject": "Arts & Design",
            "course_level": "Undergraduate",
            "degree_level": "bachelor",
            "qualification": "BA (Hons)",
            "campus": "York",
            "duration_years": 3,
            "extra_requirements": "Audition Recording (1 minute)",
            "fee_tier": "lower",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    program = response.json()
    assert program["subject"] == "Arts & Design"
    assert program["course_level"] == "Undergraduate"
    # Both vocabularies survive: course_level is what the course *is*,
    # degree_level is what an application is made at.
    assert program["degree_level"] == "bachelor"
    assert program["extra_requirements"] == "Audition Recording (1 minute)"


async def test_an_offering_without_a_course_profile_is_still_valid(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The editorial link is an enhancement, never a gate."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    university = await _university(client, headers, country["id"])

    response = await client.post(
        PROGRAMS,
        json={"university_id": university["id"], "name": "MSc Something Unmapped", "course_profile_id": None},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["course_profile_id"] is None


# ── Scholarships ─────────────────────────────────────────────────────────────


async def test_scholarship_amount_stays_prose(client: AsyncClient, user_factory, auth_headers) -> None:
    """The source values are sentences, not numbers, and the sentence is the
    part a student needs."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    response = await client.post(
        SCHOLARSHIPS,
        json={
            "slug": "ysj-early-payment",
            "name": "Early payment discount",
            "kind": "university",
            "amount": "5% EARLY PAYMENT DISCOUNT If full fees paid",
            "deadline": "Before enrolment",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == "5% EARLY PAYMENT DISCOUNT If full fees paid"


async def test_null_subjects_means_any_subject(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    created = await client.post(
        SCHOLARSHIPS, json={"slug": "open-award", "name": "Open award"}, headers=headers
    )
    assert created.json()["subjects"] is None


# ── PATCH-null semantics ─────────────────────────────────────────────────────


async def test_explicit_null_clears_a_nullable_column(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    university = await _university(client, headers, country["id"])
    assert university["tagline"] == "A university in York"

    response = await client.patch(
        f"{UNIVERSITIES}/{university['id']}", json={"tagline": None}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["tagline"] is None


async def test_nulling_a_required_column_is_a_400(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    created = await client.post(
        SCHOLARSHIPS, json={"slug": "keep-me", "name": "Keep me"}, headers=headers
    )
    response = await client.patch(
        f"{SCHOLARSHIPS}/{created.json()['id']}", json={"name": None}, headers=headers
    )
    assert response.status_code == 400


# ── CMS ──────────────────────────────────────────────────────────────────────


async def _page(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    payload = {"key": "guide.visa", "kind": "guide", "title": "The UK student visa", "slug": "visa"}
    payload.update(overrides)
    response = await client.post(PAGES, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_page_returns_its_blocks_in_order(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)

    for position, block_type in enumerate(("prose", "faq", "callout")):
        response = await client.post(
            BLOCKS,
            json={
                "page_id": page["id"],
                "block_type": block_type,
                "data": {"heading": block_type},
                "display_order": position,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text

    detail = await client.get(f"{PAGES}/{page['id']}", headers=headers)
    assert detail.status_code == 200
    assert [block["block_type"] for block in detail.json()["blocks"]] == ["prose", "faq", "callout"]


async def test_blocks_can_be_reordered_in_one_call(client: AsyncClient, user_factory, auth_headers) -> None:
    """A drag must never leave the page half-ordered."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)

    ids = []
    for position, block_type in enumerate(("prose", "faq", "callout")):
        response = await client.post(
            BLOCKS,
            json={
                "page_id": page["id"],
                "block_type": block_type,
                "data": {},
                "display_order": position,
            },
            headers=headers,
        )
        ids.append(response.json()["id"])

    reordered = await client.put(
        f"{PAGES}/{page['id']}/blocks/order",
        json={"block_ids": [ids[2], ids[0], ids[1]]},
        headers=headers,
    )
    assert reordered.status_code == 200, reordered.text
    assert [block["block_type"] for block in reordered.json()] == ["callout", "prose", "faq"]


async def test_deleting_a_page_deletes_its_blocks(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)
    await client.post(
        BLOCKS, json={"page_id": page["id"], "block_type": "prose", "data": {}}, headers=headers
    )

    deleted = await client.delete(f"{PAGES}/{page['id']}", headers=headers)
    assert deleted.status_code == 200

    remaining = await client.get(BLOCKS, headers=headers)
    assert remaining.json()["total"] == 0


async def test_block_data_is_not_constrained_by_type(client: AsyncClient, user_factory, auth_headers) -> None:
    """Adding a block type must not need a migration — that is what makes
    shipping faq/prose/callout first and the rest later a real option."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)

    response = await client.post(
        BLOCKS,
        json={
            "page_id": page["id"],
            "block_type": "component",
            "data": {"key": "cost-calculator", "props": {"currency": "GBP"}},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["key"] == "cost-calculator"


async def test_pages_filter_by_kind(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    await _page(client, headers)
    await _page(client, headers, key="home.hero", kind="fragment", title="Homepage hero", slug=None)

    guides = await client.get(f"{PAGES}?kind=guide", headers=headers)
    assert guides.json()["total"] == 1
    fragments = await client.get(f"{PAGES}?kind=fragment", headers=headers)
    assert fragments.json()["total"] == 1


# ── Blog: CRUD that never existed ────────────────────────────────────────────


async def test_blog_posts_can_finally_be_written_over_http(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """Until now the only write path for a `blog_posts` row was an import
    script."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)

    created = await client.post(
        BLOG,
        json={"slug": "how-to-apply", "title": "How to apply", "category": "Applying"},
        headers=headers,
    )
    assert created.status_code == 200, created.text

    updated = await client.patch(
        f"{BLOG}/{created.json()['id']}", json={"is_published": True}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["is_published"] is True

    deleted = await client.delete(f"{BLOG}/{created.json()['id']}", headers=headers)
    assert deleted.status_code == 200


# ── Publish gating ───────────────────────────────────────────────────────────


async def test_cannot_publish_a_university_the_site_cannot_render(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """Four fields are non-optional in the public site's own type. A gap in one
    of those is a runtime hole, not a hidden section — so it is refused rather
    than merely discouraged in the console."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)

    response = await client.post(
        UNIVERSITIES,
        json={
            "country_id": country["id"],
            "name": "Half Written University",
            "slug": "half-written",
            "is_published": True,
        },
        headers=headers,
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "city" in detail and "tagline" in detail and "overview" in detail


async def test_publish_gating_reads_the_resulting_state(client: AsyncClient, user_factory, auth_headers) -> None:
    """A PATCH that only flips the switch is still checked against what the row
    already holds."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)
    university = await _university(client, headers, country["id"])  # has city/region/tagline, no overview

    blocked = await client.patch(f"{UNIVERSITIES}/{university['id']}", json={"is_published": True}, headers=headers)
    assert blocked.status_code == 400
    assert "overview" in blocked.json()["detail"]

    filled = await client.patch(
        f"{UNIVERSITIES}/{university['id']}",
        json={"overview": "A university in York.", "is_published": True},
        headers=headers,
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["is_published"] is True


async def test_an_unpublished_record_can_stay_incomplete(client: AsyncClient, user_factory, auth_headers) -> None:
    """The gate is on publishing, not on saving. Half-finished drafts are the
    normal state of a record between an import and a staff review."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    country = await _country(client, headers)

    response = await client.post(
        UNIVERSITIES,
        json={"country_id": country["id"], "name": "Just Imported", "slug": "just-imported"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["is_published"] is False


# ── Publishing a page ────────────────────────────────────────────────────────


async def test_publishing_a_page_stamps_the_date_once(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """`published_at` is the date printed on an article. Retracting a post to
    fix a typo and putting it back must not redate a month-old piece."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)
    assert page["published_at"] is None

    published = await client.post(f"{PAGES}/{page['id']}/publish", json={"is_published": True}, headers=headers)
    assert published.status_code == 200, published.text
    assert published.json()["page"]["is_published"] is True
    first_stamp = published.json()["page"]["published_at"]
    assert first_stamp is not None

    retracted = await client.post(f"{PAGES}/{page['id']}/publish", json={"is_published": False}, headers=headers)
    assert retracted.status_code == 200
    assert retracted.json()["page"]["is_published"] is False

    again = await client.post(f"{PAGES}/{page['id']}/publish", json={"is_published": True}, headers=headers)
    assert again.status_code == 200
    assert again.json()["page"]["published_at"] == first_stamp


async def test_publishing_reports_that_the_site_was_not_told(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """With no shared secret configured — the default, and every developer
    machine — the landing is never called. The page publishes anyway and the
    response says so, rather than the console claiming a refresh that did not
    happen."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)

    response = await client.post(f"{PAGES}/{page['id']}/publish", json={"is_published": True}, headers=headers)
    assert response.status_code == 200
    assert response.json()["revalidated"] is False


async def test_counsellors_cannot_publish_a_page(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    page = await _page(client, await auth_headers(admin))

    counsellor = await user_factory(UserRole.COUNSELLOR)
    response = await client.post(
        f"{PAGES}/{page['id']}/publish", json={"is_published": True}, headers=await auth_headers(counsellor)
    )
    assert response.status_code == 403


async def test_preview_is_unavailable_without_a_shared_secret(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """No secret means no trust relationship with the public site, so there is
    nowhere to preview — a null URL rather than a link that 401s on arrival."""
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)

    response = await client.get(f"{PAGES}/{page['id']}/preview", headers=headers)
    assert response.status_code == 200
    assert response.json()["url"] is None


async def test_preview_link_is_signed_and_names_one_page(
    client: AsyncClient, user_factory, auth_headers, monkeypatch
) -> None:
    """The token is scoped to a single key, so a link forwarded to someone else
    unlocks that one draft rather than every unpublished page on the site."""
    import jwt

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "LANDING_REVALIDATE_SECRET", "shared-test-secret-long-enough-for-hs256")

    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = await _page(client, headers)

    response = await client.get(f"{PAGES}/{page['id']}/preview", headers=headers)
    assert response.status_code == 200

    url = response.json()["url"]
    assert url is not None and url.startswith(settings.LANDING_BASE_URL)

    token = parse_qs(urlparse(url).query)["token"][0]
    claims = jwt.decode(token, "shared-test-secret-long-enough-for-hs256", algorithms=[settings.JWT_ALGORITHM])
    assert claims["key"] == page["key"]
    assert claims["type"] == "preview"

    # And it must not verify against the API's own signing key.
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
