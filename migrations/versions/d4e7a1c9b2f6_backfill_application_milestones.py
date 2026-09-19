"""backfill_application_milestones

Give every offer, CAS and visa that already exists its `application_milestones`
row — the record the student portal's celebration reads.

## Why they were missing

Only `POST /applications/{id}/milestone` writes that row. Applications that
reached `offer_received` (or later) any other way have none: seeded and imported
data, and anything recorded before the milestone route existed. For those the
student's `/me/milestones/unseen` is empty however many offers they hold, so the
celebration can never fire — which is exactly what staff saw after recording an
offer on an application like that.

## What is celebrated, and what is only recorded

A row is inserted for every milestone an application has passed, so the record
is complete. Only one of them is left *unseen* — and only when it is news:

  - it is the furthest milestone the application has reached (a student whose
    visa was approved gets the visa moment, not the offer and the CAS first), and
  - it happened in the last 30 days (by the date staff recorded, falling back to
    when the application last changed).

Everything else is inserted already `seen_at = now()`, so nobody opens the
portal to confetti for an offer that arrived in the spring.

Downgrade is a no-op: once students have seen and dismissed these, deleting the
rows would re-arm celebrations, and the rows carry nothing that distinguishes a
backfilled record from a real one.

Revision ID: d4e7a1c9b2f6
Revises: c9d2f4a8e1b3
Create Date: 2026-09-19 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e7a1c9b2f6"
down_revision: str | Sequence[str] | None = "c9d2f4a8e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Statuses that have passed each milestone, and the subset where that milestone
# is the furthest one reached. `offer_declined` has passed an offer but is not
# news worth confetti; `visa_rejected` has passed the CAS.
_KINDS = [
    (
        "offer_received",
        "a.offer_received_date",
        "'offer_received','offer_accepted','offer_declined','cas_received','visa_processing','visa_rejected','visa_approved','enrolled'",
        "'offer_received','offer_accepted'",
    ),
    (
        "cas_received",
        "a.cas_received_date::text",
        "'cas_received','visa_processing','visa_rejected','visa_approved','enrolled'",
        "'cas_received','visa_processing','visa_rejected'",
    ),
    (
        "visa_approved",
        "a.visa_decision_date",
        "'visa_approved','enrolled'",
        "'visa_approved','enrolled'",
    ),
]


def upgrade() -> None:
    for kind, date_column, passed, furthest in _KINDS:
        # The date columns are ISO strings on this table (an inherited ED360
        # wart); anything that is not one falls back to `updated_at` rather than
        # failing the migration on a single malformed row.
        occurred = (
            f"COALESCE(CASE WHEN {date_column} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' "
            f"THEN substring({date_column} from 1 for 10)::date::timestamptz END, a.updated_at, now())"
        )
        op.execute(
            f"""
            INSERT INTO application_milestones (id, student_id, application_id, kind, occurred_at, seen_at, created_at)
            SELECT
                gen_random_uuid(),
                a.student_id,
                a.id,
                '{kind}'::milestone_kind,
                {occurred},
                CASE
                    WHEN a.status::text IN ({furthest}) AND {occurred} >= now() - interval '30 days' THEN NULL
                    ELSE now()
                END,
                now()
            FROM applications a
            WHERE a.status::text IN ({passed})
              AND NOT EXISTS (
                  SELECT 1 FROM application_milestones m
                  WHERE m.application_id = a.id AND m.kind = '{kind}'::milestone_kind
              )
            """
        )


def downgrade() -> None:
    # Intentionally nothing — see the module docstring.
    pass
