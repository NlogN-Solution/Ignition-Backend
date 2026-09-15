"""What to put in front of a student on Explore.

Deterministic SQL over signals the database already holds. **No model, no
scoring service, no AI** — the brief for this was explicitly "initially a
deterministic recommendation engine is enough", and it is: with ~4,800
offerings the hard problem is not ranking subtlety, it is that a student
currently gets one undifferentiated grid and has to invent their own query.

## The honesty rule

Every section here has to be able to name the reason it exists, out loud, to
the student — "Because you're interested in Computing", "Similar to the course
you're applying for". A section that cannot is not shipped. In particular
there is no "popular with students like you": that claims behavioural
collaborative filtering over a cohort, and with the data actually available it
would mean "some arbitrary offerings". `upcoming_intakes` and
`universities_you_may_like` answer real questions from real columns instead.

## Signals used

Read from the student's own profile and activity, in this order of confidence:

1. the course they pressed Apply on (`apply_intents`) — an explicit decision;
2. applications they have already opened — also explicit;
3. saved courses and universities (`student_saved_*`) — deliberate interest;
4. `StudentProfile.preferences.intendedStudyArea` / `destinations` — what they
   told onboarding;
5. `StudentProfile` education level — what they are eligible to apply for.

Nothing is inferred from anything else. Where there is no signal at all a
section is **omitted rather than filled**, which is why the response is a list
of sections and not a fixed shape: a new student's feed is honestly short.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import (
    Application,
    ApplyIntent,
    Intake,
    Program,
    StudentProfile,
    StudentSavedCourse,
    StudentSavedUniversity,
    University,
)
from ..models.enums import CourseLevel, CourseSubject

#: How many offerings a row on the feed holds. A horizontal row that scrolls
#: forever is a grid with extra steps; twelve is enough to feel browsable and
#: few enough that the section still reads as a curated answer.
ROW_SIZE = 12

#: Education level on the profile → the course levels worth showing.
#:
#: Someone who has finished school is applying for undergraduate study; someone
#: with a bachelor's is applying for a master's. Both also see the adjacent
#: preparatory routes, because a foundation year or a top-up is a real answer
#: for a student whose grades are short — but never the level *below* the
#: obvious one, which would read as being talked down to.
#: Keys are `InstitutionType` values — the enum `StudentProfile.education_level`
#: actually uses. "10+2" is Nepali/Indian school completion, which is the
#: commonest entry point in this product.
LEVEL_BY_EDUCATION: dict[str, list[CourseLevel]] = {
    "10+2": [CourseLevel.UNDERGRADUATE, CourseLevel.FOUNDATION],
    "diploma": [CourseLevel.TOP_UP, CourseLevel.UNDERGRADUATE],
    "bachelor": [CourseLevel.POSTGRADUATE, CourseLevel.INTEGRATED_MASTERS],
    "masters": [CourseLevel.POSTGRADUATE],
    "phd": [CourseLevel.POSTGRADUATE],
    # "other" is deliberately absent: it means the student told us their
    # background does not fit the list, and guessing a level from "I don't
    # fit your categories" is exactly the wrong inference. They get the
    # subject-based sections and the unpersonalised fallback instead.
}


@dataclass
class FeedSection:
    key: str
    title: str
    #: Shown under the title. This is the "why am I seeing this" line, and it
    #: is the reason the section is allowed to exist at all.
    reason: str
    programs: list[Program] = field(default_factory=list)


@dataclass
class StudentSignals:
    """Everything known about what this student is after."""

    subjects: list[Any] = field(default_factory=list)
    levels: list[CourseLevel] = field(default_factory=list)
    university_ids: list[uuid.UUID] = field(default_factory=list)
    #: Offerings to exclude from every section — already applied for, so
    #: recommending them is noise.
    applied_program_ids: set[uuid.UUID] = field(default_factory=set)
    saved_program_ids: set[uuid.UUID] = field(default_factory=set)
    #: The course the student pressed Apply on, if any.
    intent_program: Program | None = None


class RecommendationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- signal gathering ---------------------------------------------------

    async def _signals(self, student_id: uuid.UUID) -> StudentSignals:
        signals = StudentSignals()

        intent = await self.session.scalar(
            select(ApplyIntent)
            .options(selectinload(ApplyIntent.program).selectinload(Program.university))
            .where(ApplyIntent.claimed_by == student_id)
            .order_by(ApplyIntent.claimed_at.desc())
            .limit(1)
        )
        if intent is not None:
            signals.intent_program = intent.program

        applications = (
            await self.session.execute(
                select(Application.program_id, Program.subject, Program.course_level, Program.university_id)
                .join(Program, Program.id == Application.program_id)
                .where(Application.student_id == student_id)
            )
        ).all()
        for program_id, subject, level, university_id in applications:
            signals.applied_program_ids.add(program_id)
            if subject and subject not in signals.subjects:
                signals.subjects.append(subject)
            if level and level not in signals.levels:
                signals.levels.append(level)
            if university_id and university_id not in signals.university_ids:
                signals.university_ids.append(university_id)

        saved = (
            await self.session.execute(
                select(StudentSavedCourse.program_id, Program.subject, Program.university_id)
                .join(Program, Program.id == StudentSavedCourse.program_id)
                .where(StudentSavedCourse.student_id == student_id)
            )
        ).all()
        for program_id, subject, university_id in saved:
            signals.saved_program_ids.add(program_id)
            if subject and subject not in signals.subjects:
                signals.subjects.append(subject)
            if university_id and university_id not in signals.university_ids:
                signals.university_ids.append(university_id)

        saved_universities = (
            await self.session.scalars(
                select(StudentSavedUniversity.university_id).where(
                    StudentSavedUniversity.student_id == student_id
                )
            )
        ).all()
        for university_id in saved_universities:
            if university_id not in signals.university_ids:
                signals.university_ids.append(university_id)

        # The intent's own subject outranks everything gathered above — it is
        # the most recent and most explicit thing the student did.
        if signals.intent_program is not None:
            subject = signals.intent_program.subject
            if subject:
                signals.subjects = [subject] + [s for s in signals.subjects if s != subject]
            if signals.intent_program.course_level:
                level = signals.intent_program.course_level
                signals.levels = [level] + [item for item in signals.levels if item != level]

        profile = await self.session.scalar(
            select(StudentProfile).where(StudentProfile.user_id == student_id)
        )
        if profile is not None:
            if not signals.levels and profile.education_level:
                key = str(
                    profile.education_level.value
                    if hasattr(profile.education_level, "value")
                    else profile.education_level
                ).lower()
                signals.levels = LEVEL_BY_EDUCATION.get(key, [])
            # `preferences.intendedStudyArea` is free text from the onboarding
            # dropdown, not a `CourseSubject`, so it is matched against the
            # enum by name rather than coerced — a wrong coercion would
            # silently recommend the wrong subject for the life of the account.
            if not signals.subjects:
                stated = (profile.preferences or {}).get("intendedStudyArea")
                if isinstance(stated, str) and stated.strip():
                    needle = stated.strip().lower()
                    for subject in CourseSubject:
                        if subject.value.lower() == needle:
                            signals.subjects = [subject]
                            break
        return signals

    # --- section builders ---------------------------------------------------

    def _published(self) -> Select[Any]:
        return (
            select(Program)
            .join(University, Program.university_id == University.id)
            .where(Program.is_published.is_(True), University.is_published.is_(True))
            .options(
                selectinload(Program.university),
                selectinload(Program.route),
                selectinload(Program.course_profile),
            )
        )

    async def _run(self, query: Select[Any], exclude: set[uuid.UUID], limit: int = ROW_SIZE) -> list[Program]:
        if exclude:
            query = query.where(Program.id.not_in(exclude))
        result = await self.session.execute(query.order_by(Program.name).limit(limit))
        return list(result.scalars().unique().all())

    async def feed(self, student_id: uuid.UUID) -> list[FeedSection]:
        signals = await self._signals(student_id)
        # Never recommend what they have already applied for. Saved courses
        # *are* still shown — a shortlist is a maybe, and seeing it in context
        # beside similar options is the point of a feed.
        exclude = set(signals.applied_program_ids)
        sections: list[FeedSection] = []
        seen: set[uuid.UUID] = set(exclude)

        def add(section: FeedSection) -> None:
            """Keep a section only if it has something in it, and never repeat
            an offering across two rows — the same card in three sections makes
            a feed look padded."""
            fresh = [program for program in section.programs if program.id not in seen]
            if not fresh:
                return
            section.programs = fresh
            seen.update(program.id for program in fresh)
            sections.append(section)

        # 1. Similar to the course they are applying for. First, because it is
        #    the most specific thing known, and it is the section the brief
        #    calls for when a student arrives through Apply Now.
        intent_program = signals.intent_program
        if intent_program is not None and intent_program.subject:
            query = self._published().where(
                Program.subject == intent_program.subject,
                Program.id != intent_program.id,
            )
            if intent_program.course_level:
                query = query.where(Program.course_level == intent_program.course_level)
            add(
                FeedSection(
                    key="similar-to-selected",
                    title=f"Similar to {intent_program.name}",
                    reason="Same subject and level as the course you are applying for.",
                    programs=await self._run(query, exclude),
                )
            )

        # 2. Recommended for you — subject ∩ level, the two strongest signals
        #    combined. Omitted entirely when either is unknown, rather than
        #    degrading into "here is everything".
        if signals.subjects and signals.levels:
            add(
                FeedSection(
                    key="recommended",
                    title="Recommended for you",
                    reason="Matched on the subject and study level in your profile.",
                    programs=await self._run(
                        self._published().where(
                            Program.subject.in_(signals.subjects[:3]),
                            Program.course_level.in_(signals.levels[:3]),
                        ),
                        exclude,
                    ),
                )
            )

        # 3. Based on your interests — subject alone, so it can say which.
        for subject in signals.subjects[:2]:
            label = subject.value if hasattr(subject, "value") else str(subject)
            add(
                FeedSection(
                    key=f"subject-{label.lower().replace(' ', '-')}",
                    title=f"More in {label}",
                    reason=f"Because you are interested in {label}.",
                    programs=await self._run(
                        self._published().where(Program.subject == subject), exclude
                    ),
                )
            )

        # 4. More from universities they have shown interest in.
        if signals.university_ids:
            add(
                FeedSection(
                    key="from-your-universities",
                    title="More from universities you are looking at",
                    reason="Other courses at the institutions you have saved or applied to.",
                    programs=await self._run(
                        self._published().where(Program.university_id.in_(signals.university_ids[:5])),
                        exclude,
                    ),
                )
            )

        # 5. Upcoming intakes. A real question — "what can I still start this
        #    year?" — answered from real deadline columns.
        upcoming = self._published().join(Intake, Intake.program_id == Program.id).where(
            Intake.is_active.is_(True),
            Intake.application_deadline.isnot(None),
        )
        if signals.levels:
            upcoming = upcoming.where(Program.course_level.in_(signals.levels[:3]))
        add(
            FeedSection(
                key="upcoming-intakes",
                title="Closing soon",
                reason="These have an application deadline coming up.",
                programs=await self._run(upcoming, exclude),
            )
        )

        # 6. The fallback, and the only section with no personalisation at all
        #    — which is exactly what its copy says. A brand-new student with no
        #    profile, no saves and no applications gets this one section, and it
        #    does not pretend to be about them.
        if not sections:
            add(
                FeedSection(
                    key="browse",
                    title="Start exploring",
                    reason="A cross-section of the catalogue. Save a few and this page starts to fit you.",
                    programs=await self._run(self._published(), exclude),
                )
            )

        return sections

    async def similar_to(self, program_id: uuid.UUID, limit: int = ROW_SIZE) -> list[Program]:
        """"You may also be interested in", for one course.

        Used by the apply flow and the course detail page. Same subject and
        level, different offering — deliberately not "same university", which
        would answer a question the student did not ask.
        """
        program = await self.session.scalar(select(Program).where(Program.id == program_id))
        if program is None or not program.subject:
            return []
        query = self._published().where(
            Program.subject == program.subject,
            Program.id != program.id,
        )
        if program.course_level:
            query = query.where(Program.course_level == program.course_level)
        return await self._run(query, set(), limit)


async def get_recommendation_service(
    session: AsyncSession = Depends(get_db_session),
) -> RecommendationService:
    return RecommendationService(session)
