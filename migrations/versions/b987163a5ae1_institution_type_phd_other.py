"""institution_type_phd_other

Revision ID: b987163a5ae1
Revises: 8fd39f976eb4
Create Date: 2026-08-12 12:05:16.703179

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b987163a5ae1"
down_revision: Union[str, Sequence[str], None] = "8fd39f976eb4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate never detects enum *value* additions to an existing
    # Postgres type — only new/dropped columns and tables — so this is
    # hand-written. Postgres 12+ allows ADD VALUE inside a transaction as
    # long as the new label isn't used in that same transaction.
    op.execute("ALTER TYPE institution_type ADD VALUE IF NOT EXISTS 'phd'")
    op.execute("ALTER TYPE institution_type ADD VALUE IF NOT EXISTS 'other'")


def downgrade() -> None:
    # Postgres has no DROP VALUE. Downgrading this migration means recreating
    # the type without the two new labels and repointing every column that
    # uses it — safe only because nothing in this codebase has stored a 'phd'
    # or 'other' row yet; if it ever has, this downgrade will fail on the
    # CAST, which is the correct behaviour (never silently drop data).
    op.execute("ALTER TYPE institution_type RENAME TO institution_type_old")
    op.execute("CREATE TYPE institution_type AS ENUM ('bachelor', 'diploma', '10+2', 'masters')")
    op.execute(
        "ALTER TABLE student_profiles "
        "ALTER COLUMN education_level TYPE institution_type "
        "USING education_level::text::institution_type"
    )
    op.execute("DROP TYPE institution_type_old")
