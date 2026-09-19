"""journey_ends_at_visa

The student's journey ladder now ends at "Visa approved": the "Ready to depart"
milestone (key `departure`) is retired from the dashboard.

Deactivated, not deleted. `ProgressService.milestones_for` only returns active
milestones, so the step disappears from the portal and stops counting towards
the completion percentage — the remaining weights rescale on their own. The row
and any `student_milestones` already recorded against it stay, so the history of
who reached it is not destroyed and the downgrade is a single flag flip.

Revision ID: c9d2f4a8e1b3
Revises: b7c3e9d21f40
Create Date: 2026-09-19 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9d2f4a8e1b3"
down_revision: str | Sequence[str] | None = "b7c3e9d21f40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE progress_milestones SET is_active = false WHERE key = 'departure'")


def downgrade() -> None:
    op.execute("UPDATE progress_milestones SET is_active = true WHERE key = 'departure'")
