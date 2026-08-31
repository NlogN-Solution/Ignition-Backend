"""Phase 3 — load the extracted catalogue into the database.

Reads what `scripts/extract_xlsx.py` wrote into `data/catalogue/` and upserts
it. Pure JSON in, rows out; the spreadsheet is never opened here.

    python -m scripts.import_catalogue --dry-run    # report, write nothing
    python -m scripts.import_catalogue              # apply
    python -m scripts.import_catalogue --only universities

**Idempotent.** Everything upserts by slug, so a second run is a no-op. That is
a property worth protecting: this is meant to be re-run every intake cycle, and
`POST /api/v1/imports/catalogue` runs the same code from the admin panel.

**Nothing is hard-deleted.** A record that disappears from a newer extraction
is set `is_published=False` instead — an offering may already be referenced by
an application, and a university certainly is.

**Nothing is published automatically.** Rows arrive unpublished. The
spreadsheet carries the roster, the courses and the real entry criteria, but
none of the site's prose: no tagline, overview, region confirmation or
imagery. Publishing is a staff decision made in the admin panel once those
exist — see the publish gating in CATALOGUE-CMS-PLAN.md §9.3.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models import (  # noqa: E402
    Country,
    Intake,
    Program,
    University,
    UniversityRoute,
)
from app.models.enums import (  # noqa: E402
    CourseLevel,
    CourseSubject,
    DegreeLevel,
    EntryRoute,
    UkRegion,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "catalogue"

#: Where the catalogue's universities are. Created if absent — the extraction
#: is a UK intake file and every row in it is a UK institution.
DESTINATION = {"name": "United Kingdom", "iso2": "GB", "iso3": "GBR", "currency_code": "GBP"}

#: Whose entry criteria these are. The spreadsheet's academic requirements are
#: written for Nepali applicants ("Tribhuvan University 45% OR 2:2 OR Second
#: Division"), and `university_routes.applicant_country_id` is what keeps that
#: honest when a second market is added.
APPLICANT = {"name": "Nepal", "iso2": "NP", "iso3": "NPL", "currency_code": "NPR"}

#: CourseLevel -> DegreeLevel. Two vocabularies, deliberately not merged:
#: `course_level` is what a course *is* and drives the public facets;
#: `degree_level` is what an application is made at and is consumed by the
#: student portal and the applications tables.
#:
#: Foundation maps to DIPLOMA rather than BACHELOR because a foundation year is
#: a sub-degree qualification in its own right — a student on one has not yet
#: been admitted to the bachelor's it leads to.
DEGREE_LEVEL = {
    CourseLevel.FOUNDATION: DegreeLevel.DIPLOMA,
    CourseLevel.UNDERGRADUATE: DegreeLevel.BACHELOR,
    CourseLevel.TOP_UP: DegreeLevel.BACHELOR,
    CourseLevel.INTEGRATED_MASTERS: DegreeLevel.MASTER,
    CourseLevel.POSTGRADUATE: DegreeLevel.MASTER,
}


class Report:
    """What the run did, per entity, so `--dry-run` and the real run agree."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, int]] = {}

    def count(self, entity: str, action: str) -> None:
        self.rows.setdefault(entity, {"created": 0, "updated": 0, "unchanged": 0, "retired": 0})
        self.rows[entity][action] += 1

    def render(self, *, dry_run: bool) -> str:
        head = "Would apply" if dry_run else "Applied"
        lines = [f"{head}:", ""]
        lines.append(f"  {'entity':<16} {'created':>8} {'updated':>8} {'unchanged':>10} {'retired':>8}")
        for entity, counts in self.rows.items():
            lines.append(
                f"  {entity:<16} {counts['created']:>8} {counts['updated']:>8} "
                f"{counts['unchanged']:>10} {counts['retired']:>8}"
            )
        return "\n".join(lines)

    @property
    def changed(self) -> int:
        return sum(c["created"] + c["updated"] + c["retired"] for c in self.rows.values())


def load(name: str, data_dir: Path | None = None) -> list[dict[str, Any]]:
    path = (data_dir or DATA_DIR) / name
    if not path.exists():
        raise SystemExit(f"missing {path} — run `python -m scripts.extract_xlsx` first")
    result: list[dict[str, Any]] = json.loads(path.read_text())
    return result


def apply_fields(entity: Any, values: dict[str, Any]) -> bool:
    """Set only what actually differs, and say whether anything did.

    Assigning every field unconditionally would mark every row dirty on every
    run, which would make "a second run is a no-op" true of the data but false
    of `updated_at` — and the report would claim thousands of updates that
    changed nothing.
    """
    changed = False
    for key, value in values.items():
        if getattr(entity, key) != value:
            setattr(entity, key, value)
            changed = True
    return changed


async def ensure_country(session: AsyncSession, spec: dict[str, str]) -> Country:
    country = await session.scalar(select(Country).where(Country.iso2 == spec["iso2"]))
    if country is None:
        country = Country(**spec)
        session.add(country)
        await session.flush()
    return country


async def import_universities(
    session: AsyncSession, records: list[dict[str, Any]], country: Country, report: Report
) -> dict[str, University]:
    existing = {
        university.slug: university
        for university in (await session.scalars(select(University).where(University.slug.is_not(None)))).all()
    }
    by_slug: dict[str, University] = {}

    for record in records:
        slug = record["slug"]
        values = {
            "name": record["name"],
            "city": record["city"],
            "region": UkRegion(record["region"]),
            "flyer_url": record.get("flyer_url"),
            "country_id": country.id,
            # Imported rows are real, so the site's "Example data" badge comes
            # off them; anything still fictional keeps it.
            "is_example": False,
        }
        university = existing.get(slug)
        if university is None:
            university = University(slug=slug, **values)
            session.add(university)
            await session.flush()
            report.count("universities", "created")
        else:
            report.count("universities", "updated" if apply_fields(university, values) else "unchanged")
        by_slug[slug] = university

    await session.flush()
    return by_slug


async def import_routes(
    session: AsyncSession,
    records: list[dict[str, Any]],
    universities: dict[str, University],
    applicant: Country,
    report: Report,
) -> dict[tuple[str, str], UniversityRoute]:
    existing_rows = (await session.scalars(select(UniversityRoute))).all()
    existing = {(row.university_id, row.route_key.value): row for row in existing_rows}
    by_key: dict[tuple[str, str], UniversityRoute] = {}

    for record in records:
        university = universities.get(record["university_slug"])
        if university is None:
            continue
        route_key = record["route_key"]
        values = {
            "label": record.get("label"),
            "applicant_country_id": applicant.id,
            "academic_criteria": record.get("academic_criteria"),
            "english_criteria": record.get("english_criteria"),
            "english_waiver": record.get("english_waiver"),
            "fee_structure": record.get("fee_structure"),
            "scholarship_text": record.get("scholarship_text"),
            "gap_policy": record.get("gap_policy"),
            "cas_deposit": record.get("cas_deposit"),
            "enrolment_fee": record.get("enrolment_fee"),
            "deadlines": record.get("deadlines"),
            "previous_refusal": record.get("previous_refusal"),
            "extras": record.get("extras"),
            "display_order": record.get("display_order", 0),
        }
        route = existing.get((university.id, route_key))
        if route is None:
            route = UniversityRoute(
                university_id=university.id, route_key=EntryRoute(route_key), **values
            )
            session.add(route)
            await session.flush()
            report.count("routes", "created")
        else:
            report.count("routes", "updated" if apply_fields(route, values) else "unchanged")
        by_key[(record["university_slug"], route_key)] = route

    await session.flush()
    return by_key


async def import_offerings(
    session: AsyncSession,
    records: list[dict[str, Any]],
    universities: dict[str, University],
    routes: dict[tuple[str, str], UniversityRoute],
    report: Report,
) -> None:
    existing = {
        program.slug: program
        for program in (await session.scalars(select(Program).where(Program.slug.is_not(None)))).all()
    }
    seen: set[str] = set()
    intake_names: dict[str, str] = {}

    for record in records:
        university = universities.get(record["university_slug"])
        if university is None:
            continue
        slug = record["slug"]
        seen.add(slug)

        course_level = CourseLevel(record["course_level"]) if record.get("course_level") else None
        route = routes.get((record["university_slug"], record["route_key"])) if record.get("route_key") else None
        values = {
            "university_id": university.id,
            "name": record["title"],
            "qualification": record.get("qualification"),
            "subject": CourseSubject(record["subject"]) if record.get("subject") else None,
            "course_level": course_level,
            # Derived, not merged — see DEGREE_LEVEL above.
            "degree_level": DEGREE_LEVEL.get(course_level) if course_level else None,
            "campus": record.get("campus"),
            "duration_years": record.get("duration_years"),
            "placement": record.get("placement", False),
            "extra_requirements": record.get("extra_requirements"),
            "fee_tier": record.get("fee_tier"),
            "route_id": route.id if route else None,
            "is_example": False,
        }
        program = existing.get(slug)
        if program is None:
            program = Program(slug=slug, **values)
            session.add(program)
            await session.flush()
            report.count("offerings", "created")
        else:
            report.count("offerings", "updated" if apply_fields(program, values) else "unchanged")

        intake_names[slug] = record.get("intake_name") or "September 2026"
        existing[slug] = program

    # Retire what a newer extraction no longer lists, rather than deleting it:
    # an offering may already be attached to an application.
    for slug, program in existing.items():
        if slug not in seen and program.is_published:
            program.is_published = False
            report.count("offerings", "retired")

    await session.flush()
    await sync_intakes(session, existing, intake_names, report)


async def sync_intakes(
    session: AsyncSession,
    programs: dict[str, Program],
    intake_names: dict[str, str],
    report: Report,
) -> None:
    """One named intake per offering.

    The workbook's per-row intake dates are a drag-fill artefact — Excel
    serials incrementing by one down the column — so they are discarded at
    extraction. The real fact is that this is the September 2026 intake.
    """
    program_ids = [program.id for slug, program in programs.items() if slug in intake_names]
    if not program_ids:
        return
    existing_rows = (await session.scalars(select(Intake).where(Intake.program_id.in_(program_ids)))).all()
    by_program: dict[Any, list[Intake]] = {}
    for row in existing_rows:
        by_program.setdefault(row.program_id, []).append(row)

    for slug, name in intake_names.items():
        program = programs.get(slug)
        if program is None:
            continue
        rows = by_program.get(program.id, [])
        match = next((row for row in rows if row.name == name), None)
        if match is None:
            session.add(Intake(program_id=program.id, name=name))
            report.count("intakes", "created")
        else:
            report.count("intakes", "unchanged")


async def recompute_subjects(session: AsyncSession, universities: dict[str, University]) -> None:
    """Denormalise each university's subject list from its own offerings.

    The public explorer facets on this, and recomputing here is what keeps it
    true after an import adds or reclassifies courses.
    """
    for university in universities.values():
        rows = await session.scalars(
            select(Program.subject).where(Program.university_id == university.id, Program.subject.is_not(None))
        )
        subjects = sorted({subject.value for subject in rows.all() if subject})
        if university.subjects != subjects:
            university.subjects = subjects


async def import_into(
    session: AsyncSession,
    *,
    data_dir: Path | None = None,
    only: str | None = None,
    dry_run: bool = False,
) -> Report:
    """The whole import, against a caller-supplied session.

    Split out from `run` so `POST /api/v1/imports/catalogue` executes exactly
    the same code as the CLI. Without that the admin panel would grow a second
    implementation that drifts — and next September would be a developer task
    again, which is the thing this endpoint exists to prevent.
    """
    report = Report()
    destination = await ensure_country(session, DESTINATION)
    applicant = await ensure_country(session, APPLICANT)

    universities = await import_universities(session, load("universities.json", data_dir), destination, report)

    routes: dict[tuple[str, str], UniversityRoute] = {}
    if only in (None, "routes", "offerings"):
        routes = await import_routes(session, load("routes.json", data_dir), universities, applicant, report)

    if only in (None, "offerings"):
        await import_offerings(session, load("offerings.json", data_dir), universities, routes, report)
        await recompute_subjects(session, universities)

    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return report


async def run(only: str | None, dry_run: bool) -> int:
    settings = get_settings()
    if settings.is_production:
        print("Refusing to import into a production database.", file=sys.stderr)
        return 1

    async with session_factory() as session:
        report = await import_into(session, only=only, dry_run=dry_run)

    print(report.render(dry_run=dry_run))
    if dry_run:
        print("\nNothing written.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")
    parser.add_argument("--only", choices=["universities", "routes", "offerings"], default=None)
    args = parser.parse_args()
    return asyncio.run(run(args.only, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
