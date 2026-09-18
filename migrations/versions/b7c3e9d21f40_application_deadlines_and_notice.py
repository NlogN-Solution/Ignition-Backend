"""application_deadlines_and_notice

Revision ID: b7c3e9d21f40
Revises: a4d9e1b60c72
Create Date: 2026-09-18 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7c3e9d21f40"
down_revision: Union[str, Sequence[str], None] = "a4d9e1b60c72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All nullable, nothing backfilled: a deadline nobody has recorded is
    # unknown, and inventing one would put a false date in front of a student.
    op.add_column("applications", sa.Column("application_deadline", sa.Date(), nullable=True))
    op.add_column("applications", sa.Column("payment_deadline", sa.Date(), nullable=True))
    op.add_column("applications", sa.Column("condition_deadline", sa.Date(), nullable=True))
    op.add_column("applications", sa.Column("student_notice", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "student_notice")
    op.drop_column("applications", "condition_deadline")
    op.drop_column("applications", "payment_deadline")
    op.drop_column("applications", "application_deadline")
