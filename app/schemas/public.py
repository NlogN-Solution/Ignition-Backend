"""Response models for the public catalogue (CATALOGUE-CMS-PLAN.md §6).

**Every route serving these sets `response_model_exclude_none=True`.** That is
not a tidiness preference — it is the contract the public site is written
against. `Ignition-Landing/data/universities/types.ts` says it plainly:

> "Everything below is optional, and every section that renders it hides itself
> when the field is absent. That is the whole contract: a university with no
> rankings shows no rankings block rather than an empty shell."

So a university with no rankings must come back with **no `rankings` key at
all**. Return `null` or `[]` instead and the landing renders an empty section
shell — a heading with nothing under it — on every page that lacks the field.

These are separate from the admin `Read` models on purpose. The admin needs to
see that a field is empty in order to fill it in; the public site needs the
field to disappear. One model cannot do both.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

_FROM_ORM = ConfigDict(from_attributes=True)


class UniversitySummary(BaseModel):
    """A university as the explorer lists it.

    44 records, filtered client-side on the landing — a round trip per keystroke
    would only add latency — so this carries everything the facets need and
    nothing that only a detail page reads.
    """

    #: The catalogue row's own key. Present so the *student portal* can read
    #: this endpoint rather than a thinner parallel one: its shortlist tables
    #: (`student_saved_universities`, `student_saved_courses`) are keyed by
    #: UUID, while the public site addresses everything by slug. Exposing the
    #: id of a row that is already public in full costs nothing and is what
    #: lets all three portals read one catalogue. The landing ignores it — its
    #: own `University.id` is the slug.
    id: UUID
    slug: str
    name: str
    city: str | None = None
    region: str | None = None
    monogram: str | None = None
    tagline: str | None = None
    logo_url: str | None = None
    imagery: dict[str, Any] | None = None
    tuition_min: float | None = None
    tuition_max: float | None = None
    living_cost_monthly: float | None = None
    subjects: list[str] | None = None
    placement_year: bool | None = None
    course_count: int | None = None
    is_example: bool | None = None

    model_config = _FROM_ORM


class RoutePublic(BaseModel):
    """One column of the entry-criteria matrix."""

    route_key: str
    label: str | None = None
    academic_criteria: str | None = None
    english_criteria: str | None = None
    english_waiver: str | None = None
    fee_structure: str | None = None
    scholarship_text: str | None = None
    gap_policy: str | None = None
    cas_deposit: str | None = None
    enrolment_fee: str | None = None
    deadlines: str | None = None
    previous_refusal: str | None = None
    extras: dict[str, Any] | None = None

    model_config = _FROM_ORM


class ScholarshipPublic(BaseModel):
    slug: str
    name: str
    provider: str | None = None
    kind: str | None = None
    university_slug: str | None = None
    levels: list[str] | None = None
    subjects: list[str] | None = None
    nationality_group: str | None = None
    #: Prose, not a number. "£2,000 for each year (1st, 2nd & 3rd)".
    amount: str | None = None
    deadline: str | None = None
    eligibility: str | None = None
    apply_via: str | None = None
    source: dict[str, Any] | None = None
    is_example: bool | None = None

    model_config = _FROM_ORM


class UniversityDetail(UniversitySummary):
    """The full record behind `/universities/[slug]`."""

    overview: str | None = None
    student_experience: str | None = None
    careers_text: str | None = None
    website: str | None = None
    accommodation: dict[str, Any] | None = None
    entry: dict[str, Any] | None = None
    international_support: list[str] | None = None
    facilities: list[str] | None = None
    founded: str | None = None
    kind: str | None = None
    campus: str | None = None
    student_population: str | None = None
    international_students: str | None = None
    student_staff_ratio: str | None = None
    history: list[str] | None = None
    milestones: list[dict[str, Any]] | None = None
    rankings: list[dict[str, Any]] | None = None
    awards: list[dict[str, Any]] | None = None
    recognition: list[dict[str, Any]] | None = None
    employability: dict[str, Any] | None = None
    interview_profile: dict[str, Any] | None = None
    flyer_url: str | None = None
    ranking: int | None = None
    acceptance_rate: float | None = None
    faculties: list[str] | None = None
    highlights: list[str] | None = None

    routes: list[RoutePublic] | None = None
    scholarships: list[ScholarshipPublic] | None = None

    model_config = _FROM_ORM


class CourseUniversity(BaseModel):
    """The university, as an offering carries it."""

    #: See the note on `UniversitySummary.id`.
    id: UUID
    slug: str
    name: str
    city: str | None = None
    region: str | None = None

    model_config = _FROM_ORM


class CoursePublic(BaseModel):
    """One university's offering of a course — there are ~4,800 of these."""

    #: See the note on `UniversitySummary.id`.
    id: UUID
    slug: str
    title: str
    qualification: str | None = None
    subject: str | None = None
    course_level: str | None = None
    duration_years: float | None = None
    placement: bool | None = None
    campus: str | None = None
    extra_requirements: str | None = None
    fee_tier: str | None = None
    intake: str | None = None
    #: The tuition and scholarship wording from the entry route this offering
    #: was imported under, verbatim.
    #:
    #: These are on the *card* payload rather than only the detail because
    #: `programs.tuition_fee` is NULL for every one of the 4,797 offerings —
    #: the fee has only ever existed as prose on `university_routes`, and a
    #: results list that cannot say what anything costs is a results list a
    #: student has to open thirty times. 4,575 offerings reach a fee this way
    #: and 4,389 reach a scholarship.
    #:
    #: Unpublished routes are withheld, exactly as they are on the university
    #: page: a course must not become the back door to a fee staff have not
    #: signed off. The text is not parsed or tidied server-side — "LOWER TIER:
    #: £12,100 / UPPER TIER: £14,900" is one fee with a condition in it, and
    #: the condition is the part applicants get wrong.
    fee_text: str | None = None
    scholarship_text: str | None = None
    university: CourseUniversity | None = None
    course_profile_slug: str | None = None
    is_example: bool | None = None

    model_config = _FROM_ORM


class IntakePublic(BaseModel):
    """One intake of an offering, with the two dates that matter.

    Separate from `CoursePublic.intake`, which is a single display string for a
    card. A student deciding *when* to apply needs the deadline next to the
    start, and there is usually more than one.
    """

    name: str
    start_date: date | None = None
    application_deadline: date | None = None

    model_config = _FROM_ORM


class CourseUniversityProfile(BaseModel):
    """Enough of the institution to answer "where would I be studying?".

    The offering page used to end with a card that said only the university's
    name and a link. That is the right link, but it is the wrong moment to send
    someone away — the question "what is this place" is part of deciding
    whether the course is worth reading on, not a separate errand.

    This is a strict subset of `UniversityDetail`: same columns, same values,
    no derived or re-worded copy. A student who opens the university page next
    must not find a different number.
    """

    id: UUID
    slug: str
    name: str
    city: str | None = None
    region: str | None = None
    monogram: str | None = None
    tagline: str | None = None
    overview: str | None = None
    logo_url: str | None = None
    imagery: dict[str, Any] | None = None
    website: str | None = None
    founded: str | None = None
    kind: str | None = None
    campus: str | None = None
    student_population: str | None = None
    international_students: str | None = None
    student_staff_ratio: str | None = None
    ranking: int | None = None
    rankings: list[dict[str, Any]] | None = None
    facilities: list[str] | None = None
    international_support: list[str] | None = None
    accommodation: dict[str, Any] | None = None
    tuition_min: float | None = None
    tuition_max: float | None = None
    living_cost_monthly: float | None = None
    course_count: int | None = None

    model_config = _FROM_ORM


class CourseDetailPublic(CoursePublic):
    """One offering, on its own page.

    Extends the search result rather than replacing it so the card and the page
    cannot drift apart on the fields they share.

    `route` is the offering's inherited entry criteria — the `university_routes`
    row it was imported under. It is the same shape the university detail
    serves, deliberately: a student comparing the course page against the
    university's "Entry criteria by route" tab must see the same words, because
    they are the same row.

    Everything below `route` was already on `programs` and had simply never
    been served: the page rendered a title, a level and a duration while the
    row itself held requirements, key dates, outcomes and a fee. `exclude_none`
    still governs — a thin offering omits these keys entirely and its tabs
    hide rather than render empty.
    """

    university_city: str | None = None
    #: The entry criteria this course is admitted under. Absent for the 222
    #: offerings the import could not attribute to a route.
    route: RoutePublic | None = None
    #: Other offerings at the same university in the same subject.
    related: list[CoursePublic] | None = None

    # --- the offering's own record -------------------------------------------
    #: Keyed by section — {academic, documents, english}. Objects, not lists,
    #: because that is how the source data groups them.
    requirements: dict[str, Any] | None = None
    #: Keyed by milestone — {applicationOpens, applicationDeadline, ...}.
    key_dates: dict[str, Any] | None = None
    highlights: list[str] | None = None
    outcomes: list[str] | None = None
    #: Display copy ("Feb / Jul"). `intakes` below is the queryable version.
    intakes_summary: list[str] | None = None
    intakes: list[IntakePublic] | None = None
    tuition_fee: float | None = None
    currency: str | None = None
    duration_months: int | None = None
    minimum_ielts: float | None = None
    minimum_gpa: float | None = None
    course_type: str | None = None
    image_url: str | None = None

    # --- inherited context ---------------------------------------------------
    #: The institution behind the offering, so "about the university this
    #: course belongs to" is a tab rather than a link off the page.
    university_profile: CourseUniversityProfile | None = None
    #: Funding this course could plausibly draw on: the university's published
    #: awards, narrowed to those that name this course's level or subject.
    scholarships: list[ScholarshipPublic] | None = None


class CourseSearchResult(BaseModel):
    items: list[CoursePublic]
    total: int
    page: int
    limit: int


class FacetOption(BaseModel):
    """One filter option and what clicking it would leave.

    The count is the affordance: it says what the click is worth before the
    click happens, and an option that would land on zero is disabled rather
    than becoming a trap.
    """

    value: str
    label: str
    count: int


class CourseFacets(BaseModel):
    route: list[FacetOption]
    level: list[FacetOption]
    subject: list[FacetOption]
    duration: list[FacetOption]
    university: list[FacetOption]
    placement: int
    total: int


class CourseProfilePublic(BaseModel):
    slug: str
    title: str
    qualification: str | None = None
    subject: str | None = None
    course_level: str | None = None
    duration_years: float | None = None
    placement: bool | None = None
    overview: str | None = None
    what_you_study: str | None = None
    modules: list[dict[str, Any]] | None = None
    skills: list[str] | None = None
    entry: dict[str, Any] | None = None
    career_outcomes: list[str] | None = None
    related_careers: list[str] | None = None
    image_url: str | None = None
    is_example: bool | None = None

    model_config = _FROM_ORM


class CourseProfileDetail(CourseProfilePublic):
    """A profile plus where it is actually taught."""

    offering_count: int | None = None
    universities: list[CourseUniversity] | None = None


class BlockPublic(BaseModel):
    block_type: str
    data: dict[str, Any]

    model_config = _FROM_ORM


class ContentPublic(BaseModel):
    key: str
    kind: str
    slug: str | None = None
    title: str
    excerpt: str | None = None
    tag: str | None = None
    hero: dict[str, Any] | None = None
    seo: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    related: list[dict[str, Any]] | None = None
    reading_minutes: int | None = None
    published_at: datetime | None = None
    blocks: list[BlockPublic] | None = None

    model_config = _FROM_ORM


class PostPublic(BaseModel):
    slug: str
    title: str
    category: str | None = None
    author: str | None = None
    description: str | None = None
    body: str | None = None
    image_url: str | None = None
    external_url: str | None = None
    published_at: Any | None = None

    model_config = _FROM_ORM


class ListResponse(BaseModel):
    """Generic envelope for the paginated public lists.

    The API has no response envelope anywhere else — success is the bare model
    — but a list needs its total, and the landing's explorers read it.
    """

    total: int
    page: int
    limit: int


class UniversityListPublic(ListResponse):
    items: list[UniversitySummary]


class ScholarshipListPublic(ListResponse):
    items: list[ScholarshipPublic]


class CourseProfileListPublic(ListResponse):
    items: list[CourseProfilePublic]


class ContentListPublic(ListResponse):
    items: list[ContentPublic]


class PostListPublic(ListResponse):
    items: list[PostPublic]


class Taxonomies(BaseModel):
    """The controlled vocabularies, served so the landing can assert against them.

    The const tuples stay in the landing — they are what the site is written
    against — but a CI check compares them to this, so a backend enum change
    cannot silently desync a facet.
    """

    regions: list[str]
    subjects: list[str]
    course_levels: list[str]
    study_routes: list[dict[str, Any]]
    entry_routes: list[str]
