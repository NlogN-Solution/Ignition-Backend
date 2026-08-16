"""Import the real catalog from the student frontend's data files.

Phase 4. `scripts/seed.py` writes a small hand-written catalog so a fresh
database is usable; this loads the actual content the student portal's
course-search screens were designed against, into the columns
`0002_ignition_catalog` added.

    python -m scripts.import_catalog             # import
    python -m scripts.import_catalog --dry-run   # report what would change

Idempotent and upserting: countries match on name, universities on name,
programs on (university, name). Re-running refreshes the enriched fields rather
than duplicating rows, so it is safe to re-run after editing the JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models import BlogPost, Country, CountryGuide, Program, University  # noqa: E402
from app.models.enums import DegreeLevel  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[2] / "student-frontend" / "src" / "data"

#: The frontend names countries in prose, and its `code` field is not reliably
#: ISO-3166 — countryFinance.json uses "UK" where the standard is "GB". The
#: catalog keys on ISO-2, so this mapping is authoritative and the source's own
#: code is only a fallback for a country not listed here.
ISO2_BY_NAME = {
    "Canada": "CA",
    "Australia": "AU",
    "United Kingdom": "GB",
    "United States": "US",
    "Germany": "DE",
    "Nepal": "NP",
    "New Zealand": "NZ",
    "Ireland": "IE",
}

#: courses.json uses display labels for level.
DEGREE_BY_LABEL = {
    "master's": DegreeLevel.MASTER,
    "masters": DegreeLevel.MASTER,
    "bachelor's": DegreeLevel.BACHELOR,
    "bachelors": DegreeLevel.BACHELOR,
    "diploma": DegreeLevel.DIPLOMA,
    "advanced diploma": DegreeLevel.ADVANCED_DIPLOMA,
    "certificate": DegreeLevel.CERTIFICATE,
    "postgraduate diploma": DegreeLevel.POSTGRADUATE_DIPLOMA,
    "doctorate": DegreeLevel.DOCTORATE,
    "phd": DegreeLevel.DOCTORATE,
}


def _load(name: str) -> Any:
    path = DATA_DIR / name
    if not path.is_file():
        raise SystemExit(f"Missing data file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _slugify(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-").replace("--", "-")


def _months(duration: str | None) -> int | None:
    """'2 years' / '18 months' -> months. Returns None on anything else rather
    than guessing — a wrong duration is worse than a blank one."""
    if not duration:
        return None
    parts = duration.strip().lower().split()
    if len(parts) != 2 or not parts[0].replace(".", "").isdigit():
        return None
    value = float(parts[0])
    if parts[1].startswith("year"):
        return int(value * 12)
    if parts[1].startswith("month"):
        return int(value)
    return None


async def import_catalog(session: AsyncSession, dry_run: bool = False) -> dict[str, int]:
    counts = {"countries": 0, "guides": 0, "universities": 0, "programs": 0, "blog_posts": 0}

    finance = _load("countryFinance.json")
    guides = _load("countryGuides.json")
    universities = _load("universities.json")
    courses = _load("courses.json")
    blogs = _load("blogs.json")

    # --- Countries (+ finance figures) ---------------------------------------
    countries: dict[str, Country] = {}
    for name, data in finance.items():
        iso2 = ISO2_BY_NAME.get(name) or data.get("code")
        if not iso2:
            print(f"  ! skipping country {name!r}: no ISO-2 code")
            continue
        country = await session.scalar(select(Country).where(Country.iso2 == iso2))
        if country is None:
            country = Country(name=name, iso2=iso2)
            session.add(country)
            counts["countries"] += 1
        requirements = data.get("requirements") or {}
        country.currency_code = data.get("currency") or country.currency_code
        country.average_tuition_usd = requirements.get("tuitionUsd")
        country.average_living_cost_usd = requirements.get("livingCostUsd")
        country.visa_information = data.get("note")
        countries[name] = country
    await session.flush()

    # --- Country guides ------------------------------------------------------
    for key, guide_data in guides.items():
        name = guide_data.get("name") or key.title()
        country = countries.get(name) or await session.scalar(select(Country).where(Country.name == name))
        if country is None:
            print(f"  ! skipping guide {key!r}: no matching country")
            continue
        slug = _slugify(name)
        guide = await session.scalar(select(CountryGuide).where(CountryGuide.slug == slug))
        if guide is None:
            guide = CountryGuide(country_id=country.id, slug=slug, title=name)
            session.add(guide)
            counts["guides"] += 1
        about = guide_data.get("about") or {}
        guide.title = about.get("title") or name
        guide.summary = about.get("description")
        guide.about = about
        guide.is_published = True
    await session.flush()

    # --- Universities --------------------------------------------------------
    by_source_id: dict[str, University] = {}
    for record in universities:
        country_name = record.get("country")
        country = countries.get(country_name)
        if country is None:
            iso2 = ISO2_BY_NAME.get(country_name)
            country = await session.scalar(select(Country).where(Country.iso2 == iso2)) if iso2 else None
        if country is None:
            print(f"  ! skipping university {record['name']!r}: unknown country {country_name!r}")
            continue

        university = await session.scalar(select(University).where(University.name == record["name"]))
        if university is None:
            university = University(name=record["name"], country_id=country.id)
            session.add(university)
            counts["universities"] += 1
        university.country_id = country.id
        university.city = record.get("city")
        university.ranking = record.get("worldRanking")
        university.acceptance_rate = record.get("acceptanceRate")
        university.faculties = record.get("faculties")
        university.highlights = record.get("highlights")
        university.campus_type = record.get("campusType")
        university.is_partner = True
        by_source_id[record["id"]] = university
    await session.flush()

    # --- Programs ------------------------------------------------------------
    for record in courses:
        university = by_source_id.get(record.get("universityId"))
        if university is None and record.get("universityName"):
            university = await session.scalar(select(University).where(University.name == record["universityName"]))
        if university is None:
            print(f"  ! skipping course {record['name']!r}: unknown university")
            continue

        program = await session.scalar(
            select(Program).where(Program.name == record["name"], Program.university_id == university.id)
        )
        if program is None:
            program = Program(name=record["name"], university_id=university.id)
            session.add(program)
            counts["programs"] += 1
        program.degree_level = DEGREE_BY_LABEL.get((record.get("level") or "").strip().lower())
        program.field_of_study = record.get("faculty")
        program.duration_months = _months(record.get("duration"))
        program.tuition_fee = record.get("tuitionFeeUsd")
        program.currency = "USD"
        program.intakes_summary = record.get("intakes")
        program.highlights = record.get("highlights")
        program.outcomes = record.get("outcomes")
        program.requirements = record.get("requirements")
        program.key_dates = record.get("keyDates")
        program.course_type = record.get("type")
        program.image_url = record.get("image")
    await session.flush()

    # --- Blog posts ----------------------------------------------------------
    for record in blogs:
        slug = _slugify(record["title"])[:200]
        post = await session.scalar(select(BlogPost).where(BlogPost.slug == slug))
        if post is None:
            post = BlogPost(slug=slug, title=record["title"])
            session.add(post)
            counts["blog_posts"] += 1
        post.category = record.get("category")
        post.author = record.get("author")
        post.description = record.get("description")
        post.image_url = record.get("image")
        post.external_url = record.get("url")
        post.is_published = True
        raw_date = record.get("date")
        if raw_date:
            # A malformed date should not abort the whole import; the post is
            # still worth having without one.
            with suppress(ValueError):
                post.published_at = date.fromisoformat(raw_date)

    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return counts


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_production:
        print("Refusing to import into a production database.", file=sys.stderr)
        return 1

    async with session_factory() as session:
        counts = await import_catalog(session, dry_run=args.dry_run)

    verb = "Would create" if args.dry_run else "Created"
    print(f"{verb} in {settings.DB_NAME} (existing rows were updated in place):")
    for label, count in counts.items():
        print(f"  {label:<14} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
