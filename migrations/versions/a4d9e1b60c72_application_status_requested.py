"""application_status_requested

Revision ID: a4d9e1b60c72
Revises: 9b2e14c7a8d3
Create Date: 2026-09-16 16:40:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "a4d9e1b60c72"
down_revision: Union[str, Sequence[str], None] = "9b2e14c7a8d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hand-written for the same reason as e2f5a71c3d90: autogenerate never
    # detects enum *value* additions to an existing Postgres type. Postgres 12+
    # allows ADD VALUE inside a transaction provided the new label is not used
    # in that same transaction, which is why nothing below writes a 'requested'
    # row.
    #
    # `requested` is what a student-opened application starts as. Existing rows
    # are untouched: every application already in the database was either
    # opened by staff (which is acceptance) or has moved on from draft, so
    # backfilling any of them to 'requested' would invent a queue that never
    # existed.
    op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'requested'")


def downgrade() -> None:
    # No DROP VALUE in Postgres, so the type is rebuilt without the label and
    # the three columns using it are repointed. The CAST fails loudly if any
    # row has actually reached 'requested' — a downgrade must never silently
    # rewrite a real application's status.
    op.execute("ALTER TYPE application_status RENAME TO application_status_old")
    op.execute(
        "CREATE TYPE application_status AS ENUM ("
        "'draft', 'documents_pending', 'ready_to_submit', 'submitted', 'under_review', "
        "'offer_received', 'offer_accepted', 'offer_declined', 'cas_received', "
        "'visa_processing', 'visa_approved', 'visa_rejected', 'enrolled', "
        "'withdrawn', 'rejected')"
    )
    op.execute(
        "ALTER TABLE applications ALTER COLUMN status TYPE application_status "
        "USING status::text::application_status"
    )
    op.execute(
        "ALTER TABLE application_status_history ALTER COLUMN new_status TYPE application_status "
        "USING new_status::text::application_status"
    )
    op.execute(
        "ALTER TABLE application_status_history ALTER COLUMN old_status TYPE application_status "
        "USING old_status::text::application_status"
    )
    op.execute("DROP TYPE application_status_old")
