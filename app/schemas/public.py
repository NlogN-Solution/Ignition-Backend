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

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

_FROM_ORM = ConfigDict(from_attributes=True)


class UniversitySummary(BaseModel):
    """A university as the explorer lists it.

    44 records, filtered client-side on the landing — a round trip per keystroke
    would only add latency — so this carries everything the facets need and
    nothing that only a detail page reads.
    """

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

    slug: str
    name: str
    city: str | None = None
    region: str | None = None

    model_config = _FROM_ORM


class CoursePublic(BaseModel):
    """One university's offering of a course — there are ~4,800 of these."""

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
    university: CourseUniversity | None = None
    course_profile_slug: str | None = None
    is_example: bool | None = None

    model_config = _FROM_ORM


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
