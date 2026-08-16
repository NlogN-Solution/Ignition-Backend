"""notification type message

Revision ID: 7585f25a488b
Revises: 5d74cea62108
Create Date: 2026-08-12 17:55:57.792381

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7585f25a488b"
down_revision: Union[str, Sequence[str], None] = "5d74cea62108"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'message'")


def downgrade() -> None:
    op.execute("ALTER TYPE notification_type RENAME TO notification_type_old")
    op.execute(
        "CREATE TYPE notification_type AS ENUM "
        "('system', 'application', 'payment', 'appointment', 'task', 'document', 'lead')"
    )
    op.execute(
        "ALTER TABLE notifications "
        "ALTER COLUMN type TYPE notification_type "
        "USING type::text::notification_type"
    )
    op.execute("DROP TYPE notification_type_old")
