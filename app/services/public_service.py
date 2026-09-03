"""Queries behind the public catalogue (CATALOGUE-CMS-PLAN.md §6).

Two things here are not obvious and are worth reading before editing.

**Published means published.** Every query in this module filters on
`is_published`. An unpublished record is not merely hidden from a listing — it
must 404 on its own URL too, or "unpublished" only means "unlinked".

**Facets are counted leave-one-out.** See `course_facets`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, Select, Text, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import (
    BlogPost,
    ContentPage,
    CourseProfile,
    Intake,
    Program,
    Scholarship,
    University,
    UniversityRoute,
)
from ..models.enums import CourseLevel, CourseSubject, EntryRoute, UkRegion

#: The three routes a student picks between at the top of the funnel, and the
#: course levels each contains. Mirrors `studyRoutes` in
#: `Ignition-Landing/data/courses/types.ts` exactly.
#:
#: A foundation year and an integrated masters both sit inside the
#: undergraduate route because both are applied to as first degrees; putting
#: them beside "Postgraduate" as peers would misdescribe the decision.
#:
#: **These ids are a public URL contract** — the homepage search links into the
#: explorer with `?route=`, so they cannot be renamed casually.
STUDY_ROUTES: list[dict[str, Any]] = [
    {
        "id": "undergraduate",
        "label": "Undergraduate",
        "note": "Bachelor's",
        "levels": ["Undergraduate", "Foundation", "Integrated Masters"],
    },
    {"id": "postgraduate", "label": "Postgraduate", "note": "Master's", "levels": ["Postgraduate"]},
    {"id": "top-up", "label": "UG Top-Up", "note": "Final year", "levels": ["Top-Up"]},
]

STUDY_ROUTE_LEVELS = {route["id"]: route["levels"] for route in STUDY_ROUTES}

#: Durations the explorer offers, matching `durationLabel` on the landing.
DURATION_OPTIONS = ["1 year", "2 years", "3 years", "4 years", "5 years"]


def duration_label(years: float | None) -> str | None:
    if years is None:
        return None
    whole = int(years) if float(years).is_integer() else years
    return f"{whole} {'year' if whole == 1 else 'years'}"


class CourseFilters:
    """The explorer's filter state, in one object.

    Field names match `CourseExplorer`'s own filter keys so the query string,
    this class and the facet response all use one vocabulary.
    """

    __slots__ = ("q", "route", "level", "subject", "university", "placement", "duration")

    def __init__(
        self,
        q: str | None = None,
        route: str | None = None,
        level: str | None = None,
        subject: str | None = None,
        university: str | None = None,
        placement: bool | None = None,
        duration: str | None = None,
    ) -> None:
        self.q = q
        self.route = route
        self.level = level
        self.subject = subject
        self.university = university
        self.placement = placement
        self.duration = duration


class PublicCatalogueService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- universities --------------------------------------------------------

    async def universities(self) -> list[tuple[University, int]]:
        """Every published university, with its published course count.

        Not paginated: there are 44, the landing filters them client-side, and
        a page boundary would only complicate the facet counts.
        """
        counts = (
            select(Program.university_id, func.count().label("course_count"))
            .where(Program.is_published.is_(True))
            .group_by(Program.university_id)
            .subquery()
        )
        result = await self.session.execute(
            select(University, func.coalesce(counts.c.course_count, 0))
            .outerjoin(counts, counts.c.university_id == University.id)
            .where(University.is_published.is_(True))
            .order_by(University.name)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def university(self, slug: str) -> University | None:
        return await self.session.scalar(
            select(University)
            .where(University.slug == slug, University.is_published.is_(True))
            .options(selectinload(University.routes), selectinload(University.scholarships))
        )

    async def university_course_count(self, university_id: UUID) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(Program)
                .where(Program.university_id == university_id, Program.is_published.is_(True))
            )
            or 0
        )

    # --- courses -------------------------------------------------------------

    def _base(self) -> Select[Any]:
        return (
            select(Program)
            .join(University, Program.university_id == University.id)
            .where(Program.is_published.is_(True), University.is_published.is_(True))
        )

    def _conditions(self, filters: CourseFilters, *, skip: str | None = None) -> list[ColumnElement[bool]]:
        """Filter predicates, optionally omitting one facet's own.

        `skip` is what makes leave-one-out counting possible: pass a facet's
        name and every filter *except* that one is applied.
        """
        conditions: list[ColumnElement[bool]] = []

        if filters.q and filters.q.strip():
            needle = f"%{filters.q.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(Program.name).like(needle),
                    func.lower(Program.qualification).like(needle),
                    func.lower(Program.subject.cast(Text)).like(needle),
                    func.lower(University.name).like(needle),
                )
            )

        if skip != "route" and filters.route:
            levels = STUDY_ROUTE_LEVELS.get(filters.route)
            if levels:
                conditions.append(Program.course_level.in_(levels))
        if skip != "level" and filters.level:
            conditions.append(Program.course_level == filters.level)
        if skip != "subject" and filters.subject:
            conditions.append(Program.subject == filters.subject)
        if skip != "university" and filters.university:
            conditions.append(University.slug == filters.university)
        if skip != "placement" and filters.placement:
            conditions.append(Program.placement.is_(True))
        if skip != "duration" and filters.duration:
            years = _years_from_label(filters.duration)
            if years is not None:
                conditions.append(Program.duration_years == years)

        return conditions

    async def search_courses(
        self,
        filters: CourseFilters,
        page: int,
        limit: int,
        sort: str = "title",
    ) -> tuple[list[Program], int]:
        conditions = self._conditions(filters)
        query = self._base().options(selectinload(Program.university), selectinload(Program.course_profile))
        count_query = (
            select(func.count())
            .select_from(Program)
            .join(University, Program.university_id == University.id)
            .where(Program.is_published.is_(True), University.is_published.is_(True))
        )
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        order = {
            "title": Program.name,
            "university": University.name,
            "duration": Program.duration_years,
        }.get(sort, Program.name)
        query = query.order_by(order, Program.name).offset((page - 1) * limit).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().unique().all()), total

    async def course(self, slug: str) -> Program | None:
        """One published offering, with everything its own page needs.

        `route` is eager-loaded because it is the whole reason this page is
        worth having. An offering row carries almost no prose of its own — no
        tuition, no requirements, no outcomes — but 4,575 of the 4,797 point at
        a `university_routes` row, and that carries the real entry criteria,
        English requirements and fee structure for exactly this course's route.
        Without the join the page would be a title and a duration.

        The university must be published too, not just the offering. A course
        reachable at a university that is not is a page with a broken parent.
        """
        return await self.session.scalar(
            self._base()
            .where(Program.slug == slug)
            .options(
                selectinload(Program.university),
                selectinload(Program.course_profile),
                selectinload(Program.route),
            )
        )

    async def related_courses(self, program: Program, limit: int = 6) -> list[Program]:
        """Other courses in the same subject at the same university.

        Same university rather than same subject everywhere: a student on this
        page has already chosen the institution, and the useful next question
        is "what else could I study here", not "who else teaches this". The
        second question is what the explorer's filters are for.

        Returns nothing when the offering has no subject — 225 of them do not,
        and a "related courses" strip built on a NULL match would list the
        other unclassified courses, which have nothing to do with this one.
        """
        if program.subject is None:
            return []

        result = await self.session.execute(
            self._base()
            .where(
                Program.university_id == program.university_id,
                Program.subject == program.subject,
                Program.id != program.id,
            )
            .options(selectinload(Program.university), selectinload(Program.course_profile))
            .order_by(Program.name)
            .limit(limit)
        )
        return list(result.scalars().unique().all())

    async def course_facets(self, filters: CourseFilters) -> dict[str, Any]:
        """Leave-one-out facet counts.

        Each facet is counted against the set every *other* filter has already
        narrowed. Counting an option against a set its own facet has narrowed
        makes every unselected option in that facet read zero, which is both
        wrong and useless — the whole point of the number is to say what a
        click is worth *before* the click.

        Six grouped queries over ~4,800 rows; the client-side version this
        replaces was O(items x options) per facet and the code comment in
        `CourseExplorer` already anticipated the move.
        """

        async def grouped(column: Any, skip: str) -> dict[Any, int]:
            query = (
                select(column, func.count())
                .select_from(Program)
                .join(University, Program.university_id == University.id)
                .where(Program.is_published.is_(True), University.is_published.is_(True))
            )
            for condition in self._conditions(filters, skip=skip):
                query = query.where(condition)
            result = await self.session.execute(query.group_by(column))
            return {row[0]: row[1] for row in result.all()}

        by_level_for_route = await grouped(Program.course_level, "route")
        by_level = await grouped(Program.course_level, "level")
        by_subject = await grouped(Program.subject, "subject")
        by_duration = await grouped(Program.duration_years, "duration")
        by_university = await grouped(University.slug, "university")

        placement_query = (
            select(func.count())
            .select_from(Program)
            .join(University, Program.university_id == University.id)
            .where(
                Program.is_published.is_(True),
                University.is_published.is_(True),
                Program.placement.is_(True),
            )
        )
        for condition in self._conditions(filters, skip="placement"):
            placement_query = placement_query.where(condition)
        placement = await self.session.scalar(placement_query) or 0

        total_query = (
            select(func.count())
            .select_from(Program)
            .join(University, Program.university_id == University.id)
            .where(Program.is_published.is_(True), University.is_published.is_(True))
        )
        for condition in self._conditions(filters):
            total_query = total_query.where(condition)
        total = await self.session.scalar(total_query) or 0

        def level_value(key: Any) -> str:
            return key.value if isinstance(key, CourseLevel) else str(key)

        # A route's count is the sum of its levels' counts, because a route is
        # defined as a set of course levels rather than stored on the row.
        route_counts = []
        for route in STUDY_ROUTES:
            count = sum(
                value for key, value in by_level_for_route.items() if key and level_value(key) in route["levels"]
            )
            route_counts.append({"value": route["id"], "label": route["label"], "count": count})

        # Names for the university facet, so the rail does not have to hold a
        # second lookup keyed by slug.
        name_rows = await self.session.execute(
            select(University.slug, University.name).where(University.is_published.is_(True))
        )
        names: dict[str | None, str] = {row[0]: row[1] for row in name_rows.all()}  # noqa: C416

        durations: dict[str, int] = {}
        for years, count in by_duration.items():
            label = duration_label(years)
            if label:
                durations[label] = durations.get(label, 0) + count

        return {
            "route": route_counts,
            "level": [
                {"value": level.value, "label": level.value, "count": by_level.get(level, 0)}
                for level in CourseLevel
            ],
            "subject": [
                {"value": subject.value, "label": subject.value, "count": by_subject.get(subject, 0)}
                for subject in CourseSubject
            ],
            "duration": [
                {"value": option, "label": option, "count": durations.get(option, 0)}
                for option in DURATION_OPTIONS
                if durations.get(option, 0) > 0
            ],
            "university": [
                {"value": slug, "label": names.get(slug, slug), "count": count}
                for slug, count in sorted(by_university.items(), key=lambda pair: names.get(pair[0], pair[0]))
            ],
            "placement": placement,
            "total": total,
        }

    async def course_intake(self, program_id: UUID) -> str | None:
        return await self.session.scalar(
            select(Intake.name).where(Intake.program_id == program_id, Intake.is_active.is_(True)).limit(1)
        )

    # --- course profiles -----------------------------------------------------

    async def course_profiles(self, page: int, limit: int, subject: str | None = None) -> tuple[list[CourseProfile], int]:
        conditions: list[ColumnElement[bool]] = [CourseProfile.is_published.is_(True)]
        if subject:
            conditions.append(CourseProfile.subject == subject)
        query = select(CourseProfile).where(and_(*conditions)).order_by(CourseProfile.display_order, CourseProfile.title)
        total = await self.session.scalar(select(func.count()).select_from(CourseProfile).where(and_(*conditions))) or 0
        result = await self.session.execute(query.offset((page - 1) * limit).limit(limit))
        return list(result.scalars().all()), total

    async def course_profile(self, slug: str) -> CourseProfile | None:
        return await self.session.scalar(
            select(CourseProfile).where(CourseProfile.slug == slug, CourseProfile.is_published.is_(True))
        )

    async def profile_universities(self, profile_id: UUID) -> tuple[int, list[University]]:
        count = (
            await self.session.scalar(
                select(func.count())
                .select_from(Program)
                .where(Program.course_profile_id == profile_id, Program.is_published.is_(True))
            )
            or 0
        )
        result = await self.session.execute(
            select(University)
            .join(Program, Program.university_id == University.id)
            .where(
                Program.course_profile_id == profile_id,
                Program.is_published.is_(True),
                University.is_published.is_(True),
            )
            .distinct()
            .order_by(University.name)
        )
        return count, list(result.scalars().all())

    # --- scholarships --------------------------------------------------------

    async def scholarships(
        self,
        page: int,
        limit: int,
        university: str | None = None,
        level: str | None = None,
    ) -> tuple[list[tuple[Scholarship, str | None]], int]:
        query = (
            select(Scholarship, University.slug)
            .outerjoin(University, Scholarship.university_id == University.id)
            .where(Scholarship.is_published.is_(True))
        )
        count_query = select(func.count()).select_from(Scholarship).where(Scholarship.is_published.is_(True))
        if university:
            query = query.where(University.slug == university)
            count_query = count_query.where(
                Scholarship.university_id.in_(select(University.id).where(University.slug == university))
            )
        if level:
            query = query.where(Scholarship.levels.contains([level]))
            count_query = count_query.where(Scholarship.levels.contains([level]))

        total = await self.session.scalar(count_query) or 0
        result = await self.session.execute(query.order_by(Scholarship.name).offset((page - 1) * limit).limit(limit))
        return [(row[0], row[1]) for row in result.all()], total

    # --- content -------------------------------------------------------------

    async def content(self, key: str) -> ContentPage | None:
        return await self.session.scalar(
            select(ContentPage)
            .where(ContentPage.key == key, ContentPage.is_published.is_(True))
            .options(selectinload(ContentPage.blocks))
        )

    async def content_by_slug(self, kind: str, slug: str) -> ContentPage | None:
        return await self.session.scalar(
            select(ContentPage)
            .where(ContentPage.kind == kind, ContentPage.slug == slug, ContentPage.is_published.is_(True))
            .options(selectinload(ContentPage.blocks))
        )

    async def content_index(self, kind: str | None, page: int, limit: int) -> tuple[list[ContentPage], int]:
        conditions: list[ColumnElement[bool]] = [ContentPage.is_published.is_(True)]
        if kind:
            conditions.append(ContentPage.kind == kind)
        total = await self.session.scalar(select(func.count()).select_from(ContentPage).where(and_(*conditions))) or 0
        result = await self.session.execute(
            select(ContentPage)
            .where(and_(*conditions))
            .order_by(ContentPage.display_order, ContentPage.title)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def posts(self, page: int, limit: int) -> tuple[list[BlogPost], int]:
        total = (
            await self.session.scalar(
                select(func.count()).select_from(BlogPost).where(BlogPost.is_published.is_(True))
            )
            or 0
        )
        result = await self.session.execute(
            select(BlogPost)
            .where(BlogPost.is_published.is_(True))
            .order_by(BlogPost.published_at.desc().nullslast(), BlogPost.title)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def post(self, slug: str) -> BlogPost | None:
        return await self.session.scalar(
            select(BlogPost).where(BlogPost.slug == slug, BlogPost.is_published.is_(True))
        )

    # --- taxonomies ----------------------------------------------------------

    @staticmethod
    def taxonomies() -> dict[str, Any]:
        return {
            "regions": [region.value for region in UkRegion],
            "subjects": [subject.value for subject in CourseSubject],
            "course_levels": [level.value for level in CourseLevel],
            "study_routes": STUDY_ROUTES,
            "entry_routes": [route.value for route in EntryRoute],
        }

    async def published_route(self, university_slug: str, route_key: str) -> UniversityRoute | None:
        return await self.session.scalar(
            select(UniversityRoute)
            .join(University, UniversityRoute.university_id == University.id)
            .where(
                University.slug == university_slug,
                UniversityRoute.route_key == route_key,
                UniversityRoute.is_published.is_(True),
            )
        )


def _years_from_label(label: str) -> float | None:
    try:
        return float(label.split()[0])
    except (ValueError, IndexError):
        return None


async def get_public_service(session: AsyncSession = Depends(get_db_session)) -> PublicCatalogueService:
    return PublicCatalogueService(session)
