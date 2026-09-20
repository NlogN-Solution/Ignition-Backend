"""soft_delete_leads_and_applications

Deleting a lead or an application used to issue `session.delete()`, which took
the row and everything cascading off it — the lead's activity log and
follow-ups, the application's status history, its documents and its payments.
The console had no delete button at all, so the only way to reach it was the
API; adding the button without changing this would have put an irreversible,
cascading delete one mis-click away from a counsellor.

`deleted_at` makes the same action recoverable: the row stops appearing
anywhere in the product, and the history hanging off it stays intact for the
audit trail and for anyone who has to answer "what happened to that file".

Partial indexes rather than plain ones: every read filters
`deleted_at IS NULL`, so the index only ever needs the live rows.

Revision ID: a1b2c3d4e5f6
Revises: e8b3c5d7f912
Create Date: 2026-09-20 16:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "e8b3c5d7f912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("leads", "applications"):
        op.add_column(table, sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True))
        op.create_index(
            f"idx_{table}_not_deleted",
            table,
            ["id"],
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    for table in ("leads", "applications"):
        op.drop_index(f"idx_{table}_not_deleted", table_name=table)
        op.drop_column(table, "deleted_at")
