"""Rankings, awards and recognition from `university-rankings.pdf`.

Reads the curated `data/catalogue/recognition.json` and upserts it onto
existing universities, matched by slug.

    python -m scripts.import_recognition --dry-run    # report, write nothing
    python -m scripts.import_recognition              # apply

**It creates nothing.** Every record must already exist, because this document
is not a roster — it is supplementary copy about institutions the spreadsheet
already established. A slug that does not resolve is reported and skipped, not
inserted: a university invented from a PDF heading would have no country, no
courses and no entry criteria, and would be worse than the gap it filled.

**It publishes nothing.** `is_published` is never touched here, exactly as in
`import_catalogue`.

**Idempotent.** Fields are compared before assignment, so a second run reports
every record unchanged and leaves `updated_at` alone.

Four destinations, one bullet each — the routing is decided in the JSON, not
here (see the `note` field in the file):

    rankings       a placing carrying both a source and a year
    awards         an award, rating or accreditation
    employability  the graduate-employer list
    recognition    everything else, verbatim, under its own PDF heading
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
from app.models import University  # noqa: E402

from scripts.import_catalogue import Report, apply_fields  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "catalogue"
SOURCE = "recognition.json"


def load(data_dir: Path | None = None) -> dict[str, Any]:
    path = (data_dir or DATA_DIR) / SOURCE
    if not path.exists():
        raise SystemExit(f"missing {path}")
    result: dict[str, Any] = json.loads(path.read_text())
    return result


def merge_employability(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Layer the PDF's employability over whatever is already there.

    `employability` is one JSONB object holding several independent claims, so
    a blind overwrite would drop an `employedRate` a member of staff had typed
    into the admin panel just because this document happens to carry only an
    employer list. Only the keys the PDF actually supplies are replaced.
    """
    merged = dict(existing or {})
    merged.update(incoming)
    merged.setdefault("employers", [])
    merged.setdefault("services", [])
    return merged


async def import_recognition(
    session: AsyncSession,
    payload: dict[str, Any],
    report: Report,
) -> list[str]:
    """Upsert every record. Returns the slugs that did not resolve."""
    records = payload["universities"]
    slugs = [record["slug"] for record in records]

    found = {
        university.slug: university
        for university in (await session.scalars(select(University).where(University.slug.in_(slugs)))).all()
    }

    missing: list[str] = []
    for record in records:
        university = found.get(record["slug"])
        if university is None:
            missing.append(record["slug"])
            report.count("recognition", "retired")
            continue

        # An empty list in the file means "the PDF gave this institution
        # nothing of this kind". It must become NULL, not [], so the public
        # schema omits the key entirely and the site hides the section — the
        # `exclude_none` contract in app/schemas/public.py.
        values: dict[str, Any] = {
            "rankings": record.get("rankings") or None,
            "awards": record.get("awards") or None,
            "recognition": record.get("recognition") or None,
        }
        if record.get("employability"):
            values["employability"] = merge_employability(university.employability, record["employability"])

        report.count("recognition", "updated" if apply_fields(university, values) else "unchanged")

    return missing


async def import_into(
    session: AsyncSession,
    *,
    data_dir: Path | None = None,
    dry_run: bool = False,
) -> tuple[Report, list[str]]:
    report = Report()
    payload = load(data_dir)
    missing = await import_recognition(session, payload, report)

    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return report, missing


async def run(dry_run: bool, allow_production: bool) -> int:
    settings = get_settings()
    if settings.is_production and not allow_production:
        print(
            "Refusing to import into a production database. Pass --allow-production if that is what you mean.",
            file=sys.stderr,
        )
        return 1

    payload = load()
    async with session_factory() as session:
        report, missing = await import_into(session, dry_run=dry_run)

    print(report.render(dry_run=dry_run))

    if missing:
        print("\nNot in the catalogue, skipped:")
        for slug in missing:
            print(f"  {slug}")

    # The skips are a property of the source document, not of this run, so they
    # are printed every time rather than buried in the file.
    for entry in payload.get("skipped", []):
        print(f"\nSkipped in source — {entry['pdf_heading']}:\n  {entry['reason']}")

    if dry_run:
        print("\nNothing written.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="permit the write when the environment is production",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.dry_run, args.allow_production))


if __name__ == "__main__":
    raise SystemExit(main())
