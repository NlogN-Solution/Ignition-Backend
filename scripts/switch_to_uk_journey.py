"""Move existing UK applications onto the UK student journey.

New UK applications pick the `uk-student-journey` template by themselves (it is
bound to the GB country). Applications opened before it existed still run the
old standard workflow; this moves them, the same way the console's "Switch
journey" button does, one application at a time:

    python -m scripts.switch_to_uk_journey --as admin@example.com            # dry run
    python -m scripts.switch_to_uk_journey --as admin@example.com --apply

A dry run by default, because this replaces workflows on real records. What it
keeps and what it changes is `JourneyService.switch_template`: uploaded
documents stay, empty requests from the old workflow go, and stages up to the
application's current status are marked done.

Skipped: applications already on the journey, deleted ones, and ones that have
ended (rejected, withdrawn, declined, refused) — moving a closed file onto a
fresh journey would only reopen it on screen.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.db.session import session_factory  # noqa: E402
from app.models import (  # noqa: E402
    Application,
    ApplicationWorkflow,
    Country,
    Program,
    University,
    User,
    WorkflowTemplate,
)
from app.models.enums import STAFF_ROLES, ApplicationStatus  # noqa: E402
from app.services.journey_service import JourneyService  # noqa: E402
from app.services.journey_templates import UK_JOURNEY_SLUG  # noqa: E402

_ENDED = {
    ApplicationStatus.REJECTED,
    ApplicationStatus.WITHDRAWN,
    ApplicationStatus.OFFER_DECLINED,
    ApplicationStatus.VISA_REJECTED,
    ApplicationStatus.REQUEST_REJECTED,
}


async def run(actor_email: str, apply: bool) -> int:
    async with session_factory() as session:
        actor = await session.scalar(select(User).where(User.email == actor_email, User.deleted_at.is_(None)))
        if actor is None or actor.role not in STAFF_ROLES:
            print(f"No active staff account with email {actor_email!r}.")
            return 1

        template = await session.scalar(select(WorkflowTemplate).where(WorkflowTemplate.slug == UK_JOURNEY_SLUG))
        if template is None:
            print("The UK journey template does not exist. Run the migrations first.")
            return 1
        if template.country_id is None:
            print(
                "Warning: the UK journey is not bound to a country (no country with iso2 'GB' existed when it was "
                "seeded), so new UK applications will not pick it automatically. Set its country in Workflow "
                "Templates."
            )

        applications = (
            await session.scalars(
                select(Application)
                .join(Program, Program.id == Application.program_id)
                .join(University, University.id == Program.university_id)
                .join(Country, Country.id == University.country_id)
                .where(Country.iso2 == "GB", Application.deleted_at.is_(None))
                .order_by(Application.created_at)
            )
        ).all()
        workflows = {
            w.application_id: w
            for w in (
                await session.scalars(
                    select(ApplicationWorkflow)
                    .where(ApplicationWorkflow.application_id.in_([a.id for a in applications]))
                    .options(selectinload(ApplicationWorkflow.template))
                )
            ).all()
        }

        moved = skipped = 0
        for application in applications:
            current = workflows.get(application.id)
            if current is not None and current.template_id == template.id:
                skipped += 1
                continue
            if application.status in _ENDED:
                skipped += 1
                print(f"  skip   {application.id}  ({application.status.value})")
                continue
            was = current.template.name if current and current.template else "no workflow"
            print(f"  move   {application.id}  {application.status.value:<18} from {was}")
            if apply:
                await JourneyService(session).switch_template(application, template.id, actor)
            moved += 1

        verb = "Moved" if apply else "Would move"
        print(f"\n{verb} {moved} application(s); {skipped} skipped.")
        if not apply and moved:
            print("Dry run. Re-run with --apply to make the change.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--as", dest="actor", required=True, help="Email of the staff member recorded as making the switch"
    )
    parser.add_argument("--apply", action="store_true", help="Make the change (default is a dry run)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.actor, args.apply)))


if __name__ == "__main__":
    main()
