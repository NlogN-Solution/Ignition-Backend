"""request_review_and_raw_registrations

Two changes behind the same brief — registration is free, so nothing a student
does by themselves is vetted until somebody at Ignition looks at it:

- **Application requests get a decision.** `application_status` gains
  `request_rejected`, and `applications` gains the counsellor's feedback and
  who/when (`review_feedback`, `reviewed_by`, `reviewed_at`), plus
  `request_submitted_at`, which is when the student sent the request and what
  stops a repeat press notifying the desk twice.
- **A portal registration is a raw lead, not a client.** `leads.registered_at`
  records when the person signed up themselves — it can no longer be read off
  `converted_at`, because registering no longer converts. Leads the old
  behaviour auto-converted (conversion source `registration_completed`, never
  converted by a member of staff) are put back where they were before they
  registered — the status recorded on their "Registered on the portal"
  activity, or `new` — so the Clients list stops counting people nobody has
  spoken to.

Revision ID: f9a2c4e6b810
Revises: e2c4a6f8b913
Create Date: 2026-09-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a2c4e6b810"
down_revision: str | Sequence[str] | None = "e2c4a6f8b913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Hand-written: autogenerate never detects enum value additions. Postgres
    # 12+ allows ADD VALUE inside a transaction provided the new label is not
    # used in that same transaction — nothing below writes one.
    op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'request_rejected'")

    op.add_column("applications", sa.Column("review_feedback", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("reviewed_by", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_applications_reviewed_by_users",
        "applications",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("applications", sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("request_submitted_at", sa.TIMESTAMP(timezone=True), nullable=True))

    op.add_column("leads", sa.Column("registered_at", sa.TIMESTAMP(timezone=True), nullable=True))

    # Every lead that came from (or was linked by) a portal sign-up: stamp when
    # they signed up, from the account itself.
    op.execute(
        """
        UPDATE leads
           SET registered_at = COALESCE(users.created_at, leads.converted_at)
          FROM users
         WHERE users.id = leads.converted_user_id
           AND leads.conversion_source = 'registration_completed'
        """
    )

    # Undo the automatic conversion. `converted_by IS NULL` is what marks it as
    # automatic: a member of staff converting a lead is always recorded there.
    # The stage they go back to is the one the registration activity recorded
    # as `old_status` (a lead that existed before they signed up); a lead that
    # was created by the sign-up, or had been closed as lost, starts at `new`.
    op.execute(
        """
        UPDATE leads
           SET status = COALESCE(
                   (
                       SELECT la.old_status
                         FROM lead_activities la
                        WHERE la.lead_id = leads.id
                          AND la.title = 'Registered on the portal'
                          AND la.old_status IS NOT NULL
                          AND la.old_status NOT IN ('converted', 'lost')
                        ORDER BY la.created_at DESC
                        LIMIT 1
                   ),
                   'new'
               ),
               converted_at = NULL,
               conversion_source = NULL
         WHERE status = 'converted'
           AND conversion_source = 'registration_completed'
           AND converted_by IS NULL
        """
    )


def downgrade() -> None:
    # Re-convert the registrations this migration un-converted. Approximate by
    # necessity — a lead staff have since moved on by hand is indistinguishable
    # — but it restores what the old code wrote for every untouched sign-up.
    op.execute(
        """
        UPDATE leads
           SET status = 'converted',
               converted_at = registered_at,
               conversion_source = 'registration_completed'
         WHERE registered_at IS NOT NULL
           AND converted_by IS NULL
           AND status <> 'converted'
           AND status <> 'lost'
        """
    )
    op.drop_column("leads", "registered_at")

    op.drop_column("applications", "request_submitted_at")
    op.drop_column("applications", "reviewed_at")
    op.drop_constraint("fk_applications_reviewed_by_users", "applications", type_="foreignkey")
    op.drop_column("applications", "reviewed_by")
    op.drop_column("applications", "review_feedback")

    # Postgres has no DROP VALUE, so the type is recreated without the label.
    # A rejected request goes back to `requested` — the nearest state the old
    # schema can express — rather than making the cast fail.
    op.execute("UPDATE applications SET status = 'requested' WHERE status = 'request_rejected'")
    op.execute("UPDATE application_status_history SET new_status = 'requested' WHERE new_status = 'request_rejected'")
    op.execute("UPDATE application_status_history SET old_status = 'requested' WHERE old_status = 'request_rejected'")
    op.execute("ALTER TYPE application_status RENAME TO application_status_old")
    op.execute(
        "CREATE TYPE application_status AS ENUM ("
        "'draft', 'documents_pending', 'ready_to_submit', 'submitted', 'under_review', "
        "'offer_received', 'offer_accepted', 'offer_declined', 'cas_received', "
        "'visa_processing', 'visa_approved', 'visa_rejected', 'enrolled', "
        "'withdrawn', 'rejected', 'requested')"
    )
    op.execute("ALTER TABLE applications ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE applications ALTER COLUMN status TYPE application_status USING status::text::application_status"
    )
    op.execute("ALTER TABLE applications ALTER COLUMN status SET DEFAULT 'draft'")
    op.execute(
        "ALTER TABLE application_status_history ALTER COLUMN new_status TYPE application_status "
        "USING new_status::text::application_status"
    )
    op.execute(
        "ALTER TABLE application_status_history ALTER COLUMN old_status TYPE application_status "
        "USING old_status::text::application_status"
    )
    op.execute("DROP TYPE application_status_old")
