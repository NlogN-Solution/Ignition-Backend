"""Give every existing student account a lead behind it.

The admin console works one pipeline — raw lead -> prospect -> client — and the
Applicants section that used to be the only way to reach a student is gone. New
self-registrations are covered by the `StudentCreated` subscriber
(`app/core/subscribers.py`); this script covers everyone who registered before
it existed.

    python -m scripts.backfill_student_leads --dry-run
    python -m scripts.backfill_student_leads

Idempotent. A student who already has a lead pointing at them is skipped, and a
lead that matches by email is *linked* rather than duplicated — `leads.email`
carries a unique index where the address is not null, and two records for one
person is the thing this whole change exists to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.session import session_factory  # noqa: E402
from app.models import Lead, LeadActivity, User  # noqa: E402
from app.models.enums import (  # noqa: E402
    ConversionSource,
    LeadActivityType,
    LeadSource,
    LeadStatus,
    UserRole,
)


async def backfill(session: AsyncSession, dry_run: bool) -> tuple[int, int, int]:
    students = (
        (await session.execute(select(User).where(User.role == UserRole.STUDENT).order_by(User.created_at)))
        .scalars()
        .all()
    )
    linked_ids = set(
        (await session.execute(select(Lead.converted_user_id).where(Lead.converted_user_id.is_not(None))))
        .scalars()
        .all()
    )

    skipped = linked = created = 0

    for student in students:
        if student.id in linked_ids:
            skipped += 1
            continue

        existing = (
            await session.execute(select(Lead).where(Lead.email == student.email))
        ).scalar_one_or_none()

        if existing is not None:
            linked += 1
            print(f"  link   {student.email} -> lead {existing.id}")
            if not dry_run:
                old_status = existing.status
                existing.converted_user_id = student.id
                existing.status = LeadStatus.CONVERTED
                existing.converted_at = existing.converted_at or student.created_at
                if existing.conversion_source is None:
                    existing.conversion_source = ConversionSource.REGISTRATION_COMPLETED
                session.add(
                    LeadActivity(
                        lead_id=existing.id,
                        activity_type=LeadActivityType.CONVERTED,
                        title="Linked to their student account",
                        description="Matched by email during the Applicants-into-Leads backfill.",
                        old_status=old_status,
                        new_status=LeadStatus.CONVERTED,
                    )
                )
            continue

        created += 1
        print(f"  create {student.email}")
        if not dry_run:
            lead = Lead(
                first_name=student.first_name,
                last_name=student.last_name or None,
                email=student.email,
                # NOT NULL and must be non-blank; a portal signup need not have one.
                phone=(student.phone or "").strip() or "not provided",
                source=LeadSource.WEBSITE,
                status=LeadStatus.CONVERTED,
                converted_user_id=student.id,
                converted_at=student.created_at,
                conversion_source=ConversionSource.REGISTRATION_COMPLETED,
            )
            session.add(lead)
            await session.flush()
            session.add(
                LeadActivity(
                    lead_id=lead.id,
                    activity_type=LeadActivityType.LEAD_CREATED,
                    title="Created from an existing student account",
                    description="Backfilled so this student is reachable from the Leads pipeline.",
                    new_status=LeadStatus.CONVERTED,
                )
            )

    if not dry_run:
        await session.commit()

    return skipped, linked, created


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    async with session_factory() as session:
        skipped, linked, created = await backfill(session, args.dry_run)

    total = skipped + linked + created
    verb = "would be" if args.dry_run else "were"
    print(f"\n{total} students: {skipped} already had a lead, {linked} {verb} linked, {created} {verb} created.")


if __name__ == "__main__":
    asyncio.run(main())
