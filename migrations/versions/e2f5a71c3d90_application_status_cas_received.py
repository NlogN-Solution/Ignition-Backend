"""application_status_cas_received

Revision ID: e2f5a71c3d90
Revises: d1a7c93e4f20
Create Date: 2026-09-14 19:20:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "e2f5a71c3d90"
down_revision: Union[str, Sequence[str], None] = "d1a7c93e4f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate never detects enum *value* additions to an existing Postgres
    # type, so this is hand-written — same as b987163a5ae1. Postgres 12+ allows
    # ADD VALUE inside a transaction provided the new label is not used in that
    # same transaction, which is why nothing below writes a 'cas_received' row.
    #
    # No BEFORE/AFTER clause: label position in the type affects ORDER BY on the
    # enum, and nothing in this codebase orders applications by status — the
    # journey order lives in `APPLICATION_PHASES` on the client, deliberately,
    # because "which phase is this" is a product question and not a storage one.
    op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'cas_received'")


def downgrade() -> None:
    # Postgres has no DROP VALUE, so this recreates the type without the label
    # and repoints the two columns that use it. The CAST fails loudly if any
    # row has actually reached 'cas_received', which is the correct behaviour:
    # a downgrade must never silently rewrite a real application's status.
    op.execute("ALTER TYPE application_status RENAME TO application_status_old")
    op.execute(
        "CREATE TYPE application_status AS ENUM ("
        "'draft', 'documents_pending', 'ready_to_submit', 'submitted', 'under_review', "
        "'offer_received', 'offer_accepted', 'offer_declined', 'visa_processing', "
        "'visa_approved', 'visa_rejected', 'enrolled', 'withdrawn', 'rejected')"
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
