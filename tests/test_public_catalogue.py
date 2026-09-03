"""The unauthenticated public catalogue (CATALOGUE-CMS-PLAN.md §6).

The properties worth testing here are not "does CRUD work" — that is
`test_catalogue.py`'s job — but the four that the landing site's correctness
actually depends on, and that are easy to break without noticing:

1. absent fields are *omitted*, not nulled;
2. unpublished records 404 on their own URL, not merely vanish from lists;
3. facet counts are leave-one-out;
4. `limit` is clamped.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

PUBLIC = "/api/v1/public"
COUNTRIES = "/api/v1/countries"
UNIVERSITIES = "/api/v1/universities"
PROGRAMS = "/api/v1/programs"


async def _seed(client: AsyncClient, headers: dict[str, str]) -> dict:
    """One published university with three published offerings."""
    country = (await client.post(COUNTRIES, json={"name": "United Kingdom", "iso2": "GB"}, headers=headers)).json()

    university = (
        await client.post(
            UNIVERSITIES,
            json={
                "country_id": country["id"],
                "name": "York St John University",
                "slug": "york-st-john",
                "city": "York",
                "region": "England — North",
                "tagline": "A university in York",
                # The publish gate requires this: the public site reads it
                # unconditionally, so publishing without it is a runtime hole.
                "overview": "York St John has taught in the city since 1841.",
                "is_published": True,
            },
            headers=headers,
        )
    ).json()

    courses = [
        ("BA (Hons) Acting", "Arts & Design", "Undergraduate", 3, False),
        ("BSc (Hons) Computer Science", "Computing", "Undergraduate", 3, True),
        ("MSc Data Science", "Computing", "Postgraduate", 1, False),
    ]
    for title, subject, level, years, placement in courses:
        response = await client.post(
            PROGRAMS,
            json={
                "university_id": university["id"],
                "name": title,
                "slug": title.lower().replace(" ", "-").replace("(", "").replace(")", ""),
                "subject": subject,
                "course_level": level,
                "duration_years": years,
                "placement": placement,
                "is_published": True,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
    return university


# ── The hide-when-absent contract ────────────────────────────────────────────


async def test_absent_fields_are_omitted_not_nulled(client: AsyncClient, user_factory, auth_headers) -> None:
    """The landing hides any section whose field is absent. Send `null` and it
    renders an empty section shell instead — a heading with nothing under it."""
    admin = await user_factory(UserRole.ADMIN)
    await _seed(client, await auth_headers(admin))

    response = await client.get(f"{PUBLIC}/universities/york-st-john")
    assert response.status_code == 200
    body = response.json()

    assert "rankings" not in body, "a university with no rankings must return no `rankings` key"
    assert "awards" not in body
    assert "recognition" not in body
    assert "employability" not in body
    assert "routes" not in body, "no published routes means no requirements section at all"
    # What *is* set still comes through.
    assert body["tagline"] == "A university in York"
    assert body["region"] == "England — North"


async def test_recognition_reaches_the_public_detail(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """A stored field is not a served one.

    `recognition` is declared on the response model *and* assembled by hand
    into the payload dict in `routes/public.py`. Adding the column and the
    Pydantic field is not enough — miss the dict and the endpoint returns 200
    with the section silently absent, which looks exactly like a university
    that has no recognition data. This asserts the whole path.
    """
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    university = await _seed(client, headers)

    sections = [
        {
            "heading": "Sustainability progress & targets",
            "items": [
                {"label": "Carbon emissions", "detail": "Reduced carbon footprint by 50% since 2009."},
                {"label": "Waste", "sub": ["Reusable cup discounts.", "Furniture reuse schemes."]},
            ],
        }
    ]
    patch = await client.patch(
        f"{UNIVERSITIES}/{university['id']}", json={"recognition": sections}, headers=headers
    )
    assert patch.status_code == 200, patch.text

    body = (await client.get(f"{PUBLIC}/universities/york-st-john")).json()
    assert body["recognition"] == sections, "recognition must survive the hand-built payload dict"
    # Nested bullets are the shape the source document uses most, so the
    # round-trip has to keep them rather than flattening to a string.
    assert body["recognition"][0]["items"][1]["sub"] == [
        "Reusable cup discounts.",
        "Furniture reuse schemes.",
    ]


async def test_course_detail_serves_one_offering_with_its_route(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The offering page's whole reason to exist is the inherited criteria.

    An offering row carries a title, a level and a duration and nothing else a
    student can act on. What makes the page worth having is the
    `university_routes` row it was imported under, so this asserts the join and
    the criteria coming back through it — not merely a 200.
    """
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    university = await _seed(client, headers)

    route = (
        await client.post(
            "/api/v1/university-routes",
            json={
                "university_id": university["id"],
                "route_key": "undergraduate",
                "label": "UNDERGRADUATE",
                "academic_criteria": "12th Grade GPA 2.7 or 70%",
                "english_criteria": "IELTS 6.0 overall / 5.5 each band",
                "fee_structure": "£12,100",
                "is_published": True,
            },
            headers=headers,
        )
    ).json()

    programs = (await client.get(f"{PROGRAMS}?limit=100", headers=headers)).json()["items"]
    target = programs[0]
    patch = await client.patch(
        f"{PROGRAMS}/{target['id']}", json={"route_id": route["id"]}, headers=headers
    )
    assert patch.status_code == 200, patch.text

    body = (await client.get(f"{PUBLIC}/courses/{target['slug']}")).json()
    assert body["slug"] == target["slug"]
    assert body["university"]["slug"] == "york-st-john"
    assert body["route"]["academic_criteria"] == "12th Grade GPA 2.7 or 70%"
    assert body["route"]["fee_structure"] == "£12,100"

    assert (await client.get(f"{PUBLIC}/courses/no-such-course")).status_code == 404


async def test_course_detail_omits_an_unpublished_route(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """A course must not be the back door to criteria staff have not signed off.

    The university page already withholds unpublished routes. If this endpoint
    did not, the same unapproved requirement would be one URL away.
    """
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    university = await _seed(client, headers)

    route = (
        await client.post(
            "/api/v1/university-routes",
            json={
                "university_id": university["id"],
                "route_key": "undergraduate",
                "academic_criteria": "Not signed off yet",
                "is_published": False,
            },
            headers=headers,
        )
    ).json()
    target = (await client.get(f"{PROGRAMS}?limit=100", headers=headers)).json()["items"][0]
    await client.patch(f"{PROGRAMS}/{target['id']}", json={"route_id": route["id"]}, headers=headers)

    body = (await client.get(f"{PUBLIC}/courses/{target['slug']}")).json()
    assert body["slug"] == target["slug"], "the course itself still resolves"
    assert "route" not in body, "an unpublished route must not reach the public course page"


async def test_course_facets_is_not_swallowed_by_the_slug_route(client: AsyncClient) -> None:
    """`/courses/facets` is a literal path sharing a prefix with `/courses/{slug}`.

    Starlette matches in declaration order, so if the slug route is ever moved
    above it the explorer's filter counts start 404ing as "no course called
    facets" — a failure that looks like a data problem and is a routing one.
    """
    response = await client.get(f"{PUBLIC}/courses/facets")
    assert response.status_code == 200
    assert "subject" in response.json()


async def test_the_list_omits_absent_fields_too(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    await _seed(client, await auth_headers(admin))

    body = (await client.get(f"{PUBLIC}/universities")).json()
    assert body["total"] == 1
    assert "monogram" not in body["items"][0]


# ── Published means published ────────────────────────────────────────────────


async def test_unpublished_universities_are_invisible(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    university = await _seed(client, headers)

    await client.patch(f"{UNIVERSITIES}/{university['id']}", json={"is_published": False}, headers=headers)

    assert (await client.get(f"{PUBLIC}/universities")).json()["total"] == 0
    # And on its own URL — otherwise "unpublished" only means "unlinked".
    assert (await client.get(f"{PUBLIC}/universities/york-st-john")).status_code == 404
    assert (await client.get(f"{PUBLIC}/courses")).json()["total"] == 0


async def test_an_unpublished_course_disappears(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    await _seed(client, headers)

    listing = (await client.get(PROGRAMS, headers=headers)).json()
    target = listing["items"][0]
    await client.patch(f"{PROGRAMS}/{target['id']}", json={"is_published": False}, headers=headers)

    assert (await client.get(f"{PUBLIC}/courses")).json()["total"] == 2


# ── No token needed ──────────────────────────────────────────────────────────


async def test_the_public_routes_need_no_token(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    await _seed(client, await auth_headers(admin))

    for path in ("/universities", "/courses", "/courses/facets", "/scholarships", "/taxonomies", "/posts"):
        response = await client.get(f"{PUBLIC}{path}")
        assert response.status_code == 200, f"{path} answered {response.status_code}"


async def test_list_responses_are_cacheable(client: AsyncClient) -> None:
    response = await client.get(f"{PUBLIC}/taxonomies")
    assert response.headers["cache-control"] == "public, s-maxage=300, stale-while-revalidate=86400"


# ── Search and facets ────────────────────────────────────────────────────────


async def test_courses_filter_by_subject_and_route(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    await _seed(client, await auth_headers(admin))

    assert (await client.get(f"{PUBLIC}/courses?subject=Computing")).json()["total"] == 2
    # The undergraduate *route* spans three course levels, so it is not the
    # same query as level=Undergraduate.
    assert (await client.get(f"{PUBLIC}/courses?route=undergraduate")).json()["total"] == 2
    assert (await client.get(f"{PUBLIC}/courses?route=postgraduate")).json()["total"] == 1
    assert (await client.get(f"{PUBLIC}/courses?placement=true")).json()["total"] == 1


async def test_facets_are_counted_leave_one_out(client: AsyncClient, user_factory, auth_headers) -> None:
    """The count says what a click is worth *before* the click.

    With Computing selected, the subject facet must still show what the other
    subjects would give — count each option against a set its own facet has
    already narrowed and every unselected option reads zero, which is both
    wrong and useless.
    """
    admin = await user_factory(UserRole.ADMIN)
    await _seed(client, await auth_headers(admin))

    facets = (await client.get(f"{PUBLIC}/courses/facets?subject=Computing")).json()
    by_subject = {option["value"]: option["count"] for option in facets["subject"]}

    assert by_subject["Computing"] == 2
    assert by_subject["Arts & Design"] == 1, "the unselected option must still show its own count"
    assert facets["total"] == 2, "but the result total reflects every filter"


async def test_a_facet_narrowed_by_another_facet_still_updates(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    await _seed(client, await auth_headers(admin))

    facets = (await client.get(f"{PUBLIC}/courses/facets?route=postgraduate")).json()
    by_subject = {option["value"]: option["count"] for option in facets["subject"]}
    # Only the MSc is postgraduate, and it is Computing.
    assert by_subject["Computing"] == 1
    assert by_subject["Arts & Design"] == 0


async def test_route_facet_sums_its_levels(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    await _seed(client, await auth_headers(admin))

    facets = (await client.get(f"{PUBLIC}/courses/facets")).json()
    by_route = {option["value"]: option["count"] for option in facets["route"]}
    assert by_route["undergraduate"] == 2
    assert by_route["postgraduate"] == 1
    assert by_route["top-up"] == 0


# ── Pagination is clamped ────────────────────────────────────────────────────


async def test_limit_is_clamped(client: AsyncClient) -> None:
    """No max-limit exists anywhere else in this API, and `?limit=999999`
    against ~4,800 offerings is a free denial of service."""
    assert (await client.get(f"{PUBLIC}/courses?limit=999999")).status_code == 422
    assert (await client.get(f"{PUBLIC}/courses?limit=100")).status_code == 200
    assert (await client.get(f"{PUBLIC}/courses?page=0")).status_code == 422


# ── Taxonomies ───────────────────────────────────────────────────────────────


async def test_taxonomies_match_the_landing_vocabularies(client: AsyncClient) -> None:
    """A CI check on the landing compares its const tuples to this, so a
    backend enum change cannot silently desync a facet."""
    body = (await client.get(f"{PUBLIC}/taxonomies")).json()

    assert body["regions"] == [
        "England — North",
        "England — Midlands",
        "England — South",
        "Scotland",
        "Wales",
        "Northern Ireland",
    ]
    assert body["course_levels"] == [
        "Foundation",
        "Undergraduate",
        "Top-Up",
        "Integrated Masters",
        "Postgraduate",
    ]
    assert "Arts & Design" in body["subjects"]
    # The route ids are a public URL contract — the homepage links into the
    # explorer with `?route=`.
    assert [route["id"] for route in body["study_routes"]] == ["undergraduate", "postgraduate", "top-up"]


# ── Content ──────────────────────────────────────────────────────────────────


async def test_content_serves_visible_blocks_in_order(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    page = (
        await client.post(
            "/api/v1/content-pages",
            json={"key": "guide.visa", "kind": "guide", "title": "Visas", "is_published": True},
            headers=headers,
        )
    ).json()

    for order, (block_type, visible) in enumerate([("prose", True), ("faq", False), ("callout", True)]):
        await client.post(
            "/api/v1/content-blocks",
            json={
                "page_id": page["id"],
                "block_type": block_type,
                "data": {"heading": block_type},
                "display_order": order,
                "is_visible": visible,
            },
            headers=headers,
        )

    body = (await client.get(f"{PUBLIC}/content/guide.visa")).json()
    assert [block["block_type"] for block in body["blocks"]] == ["prose", "callout"]


async def test_unpublished_content_is_a_404(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    await client.post(
        "/api/v1/content-pages",
        json={"key": "guide.draft", "kind": "guide", "title": "Draft"},
        headers=headers,
    )
    assert (await client.get(f"{PUBLIC}/content/guide.draft")).status_code == 404


# ── Catalogue import ─────────────────────────────────────────────────────────


async def test_import_rejects_a_non_admin(client: AsyncClient, user_factory, auth_headers) -> None:
    """An import rewrites the whole public catalogue in one call — a wider
    blast radius than editing a record, so marketing does not get it."""
    marketing = await user_factory(UserRole.MARKETING)
    response = await client.post(
        "/api/v1/imports/catalogue",
        files={"file": ("catalogue.xlsx", b"not really a workbook")},
        data={"apply": "false"},
        headers=await auth_headers(marketing),
    )
    assert response.status_code == 403


async def test_import_rejects_an_anonymous_caller(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/imports/catalogue", files={"file": ("catalogue.xlsx", b"x")}, data={"apply": "false"}
    )
    assert response.status_code == 401


async def test_import_rejects_the_wrong_file_type(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    response = await client.post(
        "/api/v1/imports/catalogue",
        files={"file": ("notes.txt", b"hello")},
        data={"apply": "false"},
        headers=await auth_headers(admin),
    )
    assert response.status_code == 400
    assert "xlsx" in response.json()["detail"]


async def test_import_rejects_an_unreadable_workbook(client: AsyncClient, user_factory, auth_headers) -> None:
    """A file that is named .xlsx but is not one must fail as a 400, not a 500."""
    admin = await user_factory(UserRole.ADMIN)
    response = await client.post(
        "/api/v1/imports/catalogue",
        files={"file": ("catalogue.xlsx", b"definitely not a zip container")},
        data={"apply": "false"},
        headers=await auth_headers(admin),
    )
    assert response.status_code == 400
