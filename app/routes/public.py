"""The public catalogue API (CATALOGUE-CMS-PLAN.md §6).

Unauthenticated, published-only, cacheable. This is the surface
`Ignition-Landing` reads, and the entire reason the catalogue import exists:
without it the public site's data stays hand-written TypeScript.

Four properties this router has to get right, all of them easy to break:

1. **`response_model_exclude_none=True` on every route.** The public site hides
   any section whose field is absent, so an absent field must produce *no key*.
   Returning `null` renders an empty section shell instead. See
   `schemas/public.py`.
2. **Published-only, including on direct URLs.** An unpublished record 404s on
   its own slug, not merely vanishes from a list.
3. **Every route is in `PUBLIC_ENDPOINTS`.** `tests/test_endpoint_authorization.py`
   fails the build otherwise, which is the point: an unauthenticated endpoint
   cannot be added quietly.
4. **`limit` is clamped.** There is no max-limit anywhere else in this API, and
   `?limit=999999` against ~4,800 offerings is a free denial of service.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from ..api.auth import require_public
from ..api.exceptions import NotFoundException
from ..core.rate_limit import ELIGIBILITY_RATE_LIMIT, limiter
from ..models.enums import EligibilityOverall
from ..schemas.eligibility import EligibilityResultPublic, EligibilitySubmission
from ..schemas.public import (
    ContentListPublic,
    ContentPublic,
    CourseFacets,
    CourseProfileDetail,
    CourseProfileListPublic,
    CourseProfilePublic,
    CoursePublic,
    CourseSearchResult,
    CourseUniversity,
    CourseUniversityProfile,
    PostListPublic,
    PostPublic,
    CourseDetailPublic,
    IntakePublic,
    RoutePublic,
    ScholarshipListPublic,
    ScholarshipPublic,
    Taxonomies,
    UniversityDetail,
    UniversityListPublic,
    UniversitySummary,
)
from ..services.eligibility_service import EligibilityService, get_eligibility_service
from ..services.public_service import CourseFilters, PublicCatalogueService, get_public_service

router = APIRouter(prefix="/public", tags=["Public"])

#: Served from a CDN or Next's ISR, so a short shared max-age with a long
#: stale window: a visitor never waits on a revalidation, and a publish is
#: picked up within five minutes even without the on-demand webhook.
_CACHE = "public, s-maxage=300, stale-while-revalidate=86400"

#: Pagination ceiling. The explorer asks for 24 at a time.
MAX_LIMIT = 100

PageParam = Annotated[int, Query(ge=1)]
LimitParam = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _cache(response: Response) -> None:
    response.headers["Cache-Control"] = _CACHE


def _university_payload(university: Any, course_count: int | None = None) -> dict[str, Any]:
    data = {
        "id": university.id,
        "slug": university.slug,
        "name": university.name,
        "city": university.city,
        "region": university.region.value if university.region else None,
        "monogram": university.monogram,
        "tagline": university.tagline,
        "logo_url": university.logo_url,
        "imagery": university.imagery,
        "tuition_min": university.tuition_min,
        "tuition_max": university.tuition_max,
        "living_cost_monthly": university.living_cost_monthly,
        "subjects": university.subjects,
        "placement_year": university.placement_year,
        "is_example": university.is_example,
    }
    if course_count is not None:
        data["course_count"] = course_count
    return data


#: The spreadsheet's way of writing an empty cell. Printing "Scholarship: N/A"
#: states a policy the university never gave, so it is dropped like a null.
def _criterion(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return None if not cleaned or cleaned.upper() == "N/A" else cleaned


def _course_payload(program: Any, intake: str | None = None) -> dict[str, Any]:
    university = program.university
    # Withheld unless the route is published — same rule as the university
    # page. A course must not become the back door to a fee staff have not
    # signed off.
    route = program.route if getattr(program, "route", None) and program.route.is_published else None
    return {
        "id": program.id,
        "slug": program.slug,
        "title": program.name,
        "qualification": program.qualification,
        "subject": program.subject.value if program.subject else None,
        "course_level": program.course_level.value if program.course_level else None,
        "duration_years": float(program.duration_years) if program.duration_years is not None else None,
        "placement": program.placement,
        "campus": program.campus,
        "extra_requirements": program.extra_requirements,
        "fee_tier": program.fee_tier,
        "fee_text": _criterion(route.fee_structure) if route else None,
        "scholarship_text": _criterion(route.scholarship_text) if route else None,
        "intake": intake,
        "is_example": program.is_example,
        "university": CourseUniversity(
            id=university.id,
            slug=university.slug,
            name=university.name,
            city=university.city,
            region=university.region.value if university.region else None,
        )
        if university and university.slug
        else None,
        "course_profile_slug": program.course_profile.slug if program.course_profile else None,
    }


def _scholarship_payload(scholarship: Any, university_slug: str | None) -> ScholarshipPublic:
    """One award, in the shape three routes serve it in.

    The university page, the offering page and `/scholarships` all render the
    same record. Building it in one place is what stops them drifting into
    three subtly different funding tables.
    """
    return ScholarshipPublic(
        slug=scholarship.slug,
        name=scholarship.name,
        provider=scholarship.provider,
        kind=scholarship.kind,
        university_slug=university_slug,
        levels=scholarship.levels,
        subjects=scholarship.subjects,
        nationality_group=scholarship.nationality_group,
        amount=scholarship.amount,
        deadline=scholarship.deadline,
        eligibility=scholarship.eligibility,
        apply_via=scholarship.apply_via,
        source=scholarship.source,
        is_example=scholarship.is_example,
    )


def _route_payload(route: Any) -> RoutePublic:
    """One column of the entry-criteria matrix.

    Shared by the university page and the offering page on purpose: an
    offering's criteria *are* the university's route row, and a student who
    checks one against the other must find the same words.
    """
    return RoutePublic(
        route_key=route.route_key.value,
        label=route.label,
        academic_criteria=route.academic_criteria,
        english_criteria=route.english_criteria,
        english_waiver=route.english_waiver,
        fee_structure=route.fee_structure,
        scholarship_text=route.scholarship_text,
        gap_policy=route.gap_policy,
        cas_deposit=route.cas_deposit,
        enrolment_fee=route.enrolment_fee,
        deadlines=route.deadlines,
        previous_refusal=route.previous_refusal,
        extras=route.extras,
    )


# --- universities ------------------------------------------------------------


@router.get(
    "/universities",
    response_model=UniversityListPublic,
    response_model_exclude_none=True,
    summary="Published universities",
)
async def public_universities(
    response: Response,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> UniversityListPublic:
    """All of them, unpaginated — there are 44 and the explorer filters them
    client-side, where a round trip per keystroke would only add latency."""
    _cache(response)
    rows = await service.universities()
    items = [UniversitySummary(**_university_payload(university, count)) for university, count in rows]
    return UniversityListPublic(items=items, total=len(items), page=1, limit=len(items))


@router.get(
    "/universities/{slug}",
    response_model=UniversityDetail,
    response_model_exclude_none=True,
    summary="A published university",
)
async def public_university(
    slug: str,
    response: Response,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> UniversityDetail:
    _cache(response)
    university = await service.university(slug)
    if university is None:
        raise NotFoundException("University not found")

    count = await service.university_course_count(university.id)
    payload = _university_payload(university, count)
    payload.update(
        {
            "overview": university.overview,
            "student_experience": university.student_experience,
            "careers_text": university.careers_text,
            "website": university.website,
            "accommodation": university.accommodation,
            "entry": university.entry,
            "international_support": university.international_support,
            "facilities": university.facilities,
            "founded": university.founded,
            "kind": university.kind,
            "campus": university.campus,
            "student_population": university.student_population,
            "international_students": university.international_students,
            "student_staff_ratio": university.student_staff_ratio,
            "history": university.history,
            "milestones": university.milestones,
            "rankings": university.rankings,
            "awards": university.awards,
            "recognition": university.recognition,
            "employability": university.employability,
            "interview_profile": university.interview_profile,
            "flyer_url": university.flyer_url,
            "ranking": university.ranking,
            "acceptance_rate": university.acceptance_rate,
            "faculties": university.faculties,
            "highlights": university.highlights,
        }
    )

    routes = [
        _route_payload(route)
        for route in sorted(university.routes, key=lambda item: item.display_order)
        if route.is_published
    ]
    scholarships = [
        _scholarship_payload(item, university.slug) for item in university.scholarships if item.is_published
    ]

    # Empty lists are omitted rather than sent: a university with no published
    # routes must render no requirements section, not an empty one.
    payload["routes"] = routes or None
    payload["scholarships"] = scholarships or None
    return UniversityDetail(**payload)


# --- courses -----------------------------------------------------------------


@router.get(
    "/courses",
    response_model=CourseSearchResult,
    response_model_exclude_none=True,
    summary="Search course offerings",
)
async def public_courses(
    response: Response,
    q: str | None = None,
    route: str | None = None,
    level: str | None = None,
    subject: str | None = None,
    university: str | None = None,
    placement: bool | None = None,
    duration: str | None = None,
    sort: str = "title",
    page: PageParam = 1,
    limit: LimitParam = 24,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> CourseSearchResult:
    """The explorer's result set.

    Parameter names mirror `CourseExplorer`'s filter keys exactly, and `route`
    carries the ids the homepage search links into — a public URL contract.
    """
    _cache(response)
    filters = CourseFilters(
        q=q, route=route, level=level, subject=subject, university=university, placement=placement, duration=duration
    )
    programs, total = await service.search_courses(filters, page, limit, sort=sort)
    items = [CoursePublic(**_course_payload(program)) for program in programs]
    return CourseSearchResult(items=items, total=total, page=page, limit=limit)


@router.get(
    "/courses/facets",
    response_model=CourseFacets,
    response_model_exclude_none=True,
    summary="Leave-one-out facet counts",
)
async def public_course_facets(
    response: Response,
    q: str | None = None,
    route: str | None = None,
    level: str | None = None,
    subject: str | None = None,
    university: str | None = None,
    placement: bool | None = None,
    duration: str | None = None,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> CourseFacets:
    """What each filter option would leave if it were clicked.

    Declared before `/courses/{slug}` would be, so a literal path segment can
    never be swallowed as a slug.
    """
    _cache(response)
    filters = CourseFilters(
        q=q, route=route, level=level, subject=subject, university=university, placement=placement, duration=duration
    )
    return CourseFacets(**await service.course_facets(filters))


@router.get(
    "/courses/{slug}",
    response_model=CourseDetailPublic,
    response_model_exclude_none=True,
    summary="One published course offering",
)
async def public_course(
    slug: str,
    response: Response,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> CourseDetailPublic:
    """One of the ~4,800 offerings, on its own URL.

    **Declared after `/courses/facets` on purpose.** Starlette matches in
    declaration order, so putting this first would make `{slug}` swallow
    `facets` and break the explorer's filter counts with a 404 for a course
    called "facets".
    """
    _cache(response)
    program = await service.course(slug)
    if program is None:
        raise NotFoundException("Course not found")

    university = program.university
    payload = _course_payload(program, await service.course_intake(program.id))
    payload["university_city"] = university.city if university else None

    # The criteria this course is admitted under. Unpublished routes are
    # withheld exactly as they are on the university page — a course must not
    # become the back door to a requirement staff have not signed off.
    route = program.route
    if route is not None and route.is_published:
        payload["route"] = _route_payload(route)

    # The offering's own columns. Every one of these was already on `programs`
    # and had never been served, which is why the page had a spec list and
    # nothing else. Empty containers are normalised to None so `exclude_none`
    # drops the key and the tab hides, rather than rendering a heading over
    # nothing.
    payload.update(
        {
            "requirements": program.requirements or None,
            "key_dates": program.key_dates or None,
            "highlights": program.highlights or None,
            "outcomes": program.outcomes or None,
            "intakes_summary": program.intakes_summary or None,
            "tuition_fee": float(program.tuition_fee) if program.tuition_fee is not None else None,
            "currency": program.currency,
            "duration_months": program.duration_months,
            "minimum_ielts": float(program.minimum_ielts) if program.minimum_ielts is not None else None,
            "minimum_gpa": float(program.minimum_gpa) if program.minimum_gpa is not None else None,
            "course_type": program.course_type,
            "image_url": program.image_url,
        }
    )

    intakes = await service.course_intakes(program.id)
    payload["intakes"] = [IntakePublic.model_validate(intake) for intake in intakes] or None

    if university is not None:
        scholarships = await service.course_scholarships(program)
        payload["scholarships"] = [
            _scholarship_payload(item, university.slug) for item in scholarships
        ] or None

        # "About the university this course belongs to", as a tab rather than
        # a link off the page. A strict subset of what `/universities/{slug}`
        # serves — same columns, no re-worded copy — so the two cannot state
        # different facts about one institution.
        payload["university_profile"] = CourseUniversityProfile(
            id=university.id,
            slug=university.slug,
            name=university.name,
            city=university.city,
            region=university.region.value if university.region else None,
            monogram=university.monogram,
            tagline=university.tagline,
            overview=university.overview,
            logo_url=university.logo_url,
            imagery=university.imagery,
            website=university.website,
            founded=university.founded,
            kind=university.kind,
            campus=university.campus,
            student_population=university.student_population,
            international_students=university.international_students,
            student_staff_ratio=university.student_staff_ratio,
            ranking=university.ranking,
            rankings=university.rankings or None,
            facilities=university.facilities or None,
            international_support=university.international_support or None,
            accommodation=university.accommodation or None,
            tuition_min=university.tuition_min,
            tuition_max=university.tuition_max,
            living_cost_monthly=university.living_cost_monthly,
            course_count=await service.university_course_count(university.id),
        )

    related = await service.related_courses(program)
    if related:
        payload["related"] = [CoursePublic(**_course_payload(item)) for item in related]

    return CourseDetailPublic(**payload)


# --- course profiles ---------------------------------------------------------


@router.get(
    "/course-profiles",
    response_model=CourseProfileListPublic,
    response_model_exclude_none=True,
    summary="Published course profiles",
)
async def public_course_profiles(
    response: Response,
    subject: str | None = None,
    page: PageParam = 1,
    limit: LimitParam = 50,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> CourseProfileListPublic:
    _cache(response)
    profiles, total = await service.course_profiles(page, limit, subject=subject)
    items = [
        CourseProfilePublic.model_validate(
            {
                **{key: getattr(profile, key) for key in CourseProfilePublic.model_fields if hasattr(profile, key)},
                "subject": profile.subject.value if profile.subject else None,
                "course_level": profile.course_level.value if profile.course_level else None,
                "duration_years": float(profile.duration_years) if profile.duration_years is not None else None,
            }
        )
        for profile in profiles
    ]
    return CourseProfileListPublic(items=items, total=total, page=page, limit=limit)


@router.get(
    "/course-profiles/{slug}",
    response_model=CourseProfileDetail,
    response_model_exclude_none=True,
    summary="A published course profile",
)
async def public_course_profile(
    slug: str,
    response: Response,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> CourseProfileDetail:
    _cache(response)
    profile = await service.course_profile(slug)
    if profile is None:
        raise NotFoundException("Course profile not found")

    count, universities = await service.profile_universities(profile.id)
    payload = {key: getattr(profile, key) for key in CourseProfilePublic.model_fields if hasattr(profile, key)}
    payload.update(
        {
            "subject": profile.subject.value if profile.subject else None,
            "course_level": profile.course_level.value if profile.course_level else None,
            "duration_years": float(profile.duration_years) if profile.duration_years is not None else None,
            "offering_count": count,
            "universities": [
                CourseUniversity(
                    id=university.id,
                    slug=university.slug,
                    name=university.name,
                    city=university.city,
                    region=university.region.value if university.region else None,
                )
                for university in universities
                if university.slug
            ]
            or None,
        }
    )
    return CourseProfileDetail(**payload)


# --- scholarships ------------------------------------------------------------


@router.get(
    "/scholarships",
    response_model=ScholarshipListPublic,
    response_model_exclude_none=True,
    summary="Published scholarships",
)
async def public_scholarships(
    response: Response,
    university: str | None = None,
    level: str | None = None,
    page: PageParam = 1,
    limit: LimitParam = 50,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> ScholarshipListPublic:
    _cache(response)
    rows, total = await service.scholarships(page, limit, university=university, level=level)
    items = [_scholarship_payload(scholarship, university_slug) for scholarship, university_slug in rows]
    return ScholarshipListPublic(items=items, total=total, page=page, limit=limit)


# --- content -----------------------------------------------------------------


def _content_payload(page: Any, *, with_blocks: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": page.key,
        "kind": page.kind.value,
        "slug": page.slug,
        "title": page.title,
        "excerpt": page.excerpt,
        "tag": page.tag,
        "hero": page.hero,
        "seo": page.seo,
        "source": page.source,
        "related": page.related,
        "reading_minutes": page.reading_minutes,
        "published_at": page.published_at,
    }
    if with_blocks:
        blocks = [
            {"block_type": block.block_type.value, "data": block.data}
            for block in sorted(page.blocks, key=lambda item: (item.display_order, item.created_at))
            if block.is_visible
        ]
        payload["blocks"] = blocks or None
    return payload


@router.get(
    "/content",
    response_model=ContentListPublic,
    response_model_exclude_none=True,
    summary="Published content index",
)
async def public_content_index(
    response: Response,
    kind: str | None = None,
    page: PageParam = 1,
    limit: LimitParam = 50,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> ContentListPublic:
    """Drives `/resources/guides`, which is a hardcoded array in the page today."""
    _cache(response)
    pages, total = await service.content_index(kind, page, limit)
    items = [ContentPublic(**_content_payload(item, with_blocks=False)) for item in pages]
    return ContentListPublic(items=items, total=total, page=page, limit=limit)


@router.get(
    "/content/{key}",
    response_model=ContentPublic,
    response_model_exclude_none=True,
    summary="A published page with its blocks",
)
async def public_content(
    key: str,
    response: Response,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> ContentPublic:
    _cache(response)
    content = await service.content(key)
    if content is None:
        raise NotFoundException("Content not found")
    return ContentPublic(**_content_payload(content))


# --- posts -------------------------------------------------------------------


@router.get("/posts", response_model=PostListPublic, response_model_exclude_none=True, summary="Published posts")
async def public_posts(
    response: Response,
    page: PageParam = 1,
    limit: LimitParam = 20,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> PostListPublic:
    _cache(response)
    posts, total = await service.posts(page, limit)
    return PostListPublic(
        items=[PostPublic.model_validate(post) for post in posts], total=total, page=page, limit=limit
    )


@router.get(
    "/posts/{slug}", response_model=PostPublic, response_model_exclude_none=True, summary="A published post"
)
async def public_post(
    slug: str,
    response: Response,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> PostPublic:
    _cache(response)
    post = await service.post(slug)
    if post is None:
        raise NotFoundException("Post not found")
    return PostPublic.model_validate(post)


# --- eligibility -------------------------------------------------------------


@router.post(
    "/eligibility",
    response_model=EligibilityResultPublic,
    summary="Submit a preliminary eligibility assessment",
)
@limiter.limit(ELIGIBILITY_RATE_LIMIT)
async def submit_eligibility(
    request: Request,
    payload: EligibilitySubmission,
    service: EligibilityService = Depends(get_eligibility_service),
    _: None = Depends(require_public),
) -> EligibilityResultPublic:
    """The one endpoint on this site that writes.

    Everything else under `/public` is a cacheable GET over published records;
    this takes a stranger's personal details and creates a lead, so it is the
    only one that needs a rate limit and the only one whose response is
    deliberately narrower than what it stored. The caller gets a reference, a
    status and a sentence — not the reasoning, not the lead id, and not
    anything a competitor could use to map how the funnel scores people.

    The assessment itself is computed here, server-side. The wizard shows
    encouragement as a student fills it in; nothing it displays is trusted.
    """
    assessment = await service.submit(payload.model_dump(mode="json"))

    return EligibilityResultPublic(
        # The lead's own id would leak the CRM's key to an anonymous caller.
        # A short reference is what a student quotes on the phone anyway.
        reference=str(assessment.id)[:8].upper(),
        overall_status=assessment.overall_status,
        document_readiness=assessment.document_readiness,
        summary=_STUDENT_SUMMARY[assessment.overall_status],
    )


#: What the student is told, per verdict. Written for someone who has just
#: spent three minutes on a form and is hoping for good news: honest about
#: what has and has not been established, and never final.
_STUDENT_SUMMARY = {
    EligibilityOverall.PRELIMINARY_LIKELY_ELIGIBLE: (
        "Based on the information you provided, your profile appears to meet several of the "
        "requirements UK universities typically ask for. A counsellor will review it and contact "
        "you with the next steps."
    ),
    EligibilityOverall.NEEDS_COUNSELLOR_REVIEW: (
        "Thanks — there are a few things worth talking through before we can give you a clear "
        "picture. A counsellor will review what you have told us and get in touch to explain your "
        "options, including routes you may not have considered."
    ),
    EligibilityOverall.MORE_INFORMATION_REQUIRED: (
        "Thanks — we have what you have told us so far, and we need a little more before we can "
        "give you a useful answer. A counsellor will contact you to fill in the gaps. Nothing here "
        "counts against you."
    ),
}


# --- taxonomies --------------------------------------------------------------


@router.get("/taxonomies", response_model=Taxonomies, summary="Controlled vocabularies")
async def public_taxonomies(
    response: Response,
    service: PublicCatalogueService = Depends(get_public_service),
    _: None = Depends(require_public),
) -> Taxonomies:
    """The facet vocabularies, served so the landing can assert it agrees.

    The const tuples stay in the landing — they are what the site is written
    against — but a CI check compares them to this, so a backend enum change
    cannot silently desync a facet.
    """
    _cache(response)
    return Taxonomies(**service.taxonomies())
