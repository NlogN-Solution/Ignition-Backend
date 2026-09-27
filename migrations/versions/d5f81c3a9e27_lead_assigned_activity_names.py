"""lead_assigned_activity_names

Revision ID: d5f81c3a9e27
Revises: c7a2e9f14b58
Create Date: 2026-09-27 15:00:00.000000

"Lead assigned" activities used to read "Lead assigned to user <uuid>.". New
ones name the assignee (services/lead_service.py). This rewrites the existing
rows the same way, where the user still exists. Downgrade is a no-op: the
wording is cosmetic and the id is still on the lead.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d5f81c3a9e27"
down_revision: str | Sequence[str] | None = "c7a2e9f14b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE lead_activities AS a
        SET description = 'Lead assigned to '
            || COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''), u.email)
            || '.'
        FROM users AS u
        WHERE a.activity_type = 'assigned'
          AND a.description ~ '^Lead assigned to user [0-9a-fA-F-]{36}\\.$'
          AND u.id = substring(a.description FROM 'user ([0-9a-fA-F-]{36})')::uuid
        """
    )


def downgrade() -> None:
    pass
