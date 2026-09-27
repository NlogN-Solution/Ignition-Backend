"""drop_seeded_student_checklist

Revision ID: c7a2e9f14b58
Revises: b4e1d9c2a7f3
Create Date: 2026-09-27 12:00:00.000000

The student checklist no longer starts pre-filled with the nine-step template
ladder (passport → … → departure). What a student owes depends on their route
and workflow, so it now holds only tasks their counsellor set and any they wrote
themselves (see app/services/checklist_service.py).

This removes every student's copy of a template rung — rows with a template id
or a key; counsellor tasks and the student's own items have neither — and
deactivates the template so nothing can materialise it again.

Downgrade reactivates the template only. The deleted copies (and whether a
student had ticked them) are not recoverable, and the code that re-created them
on first read is gone, so a downgrade does not bring the ladder back by itself.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7a2e9f14b58"
down_revision: str | Sequence[str] | None = "b4e1d9c2a7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM student_checklist_items "
        "WHERE (template_item_id IS NOT NULL OR key IS NOT NULL) "
        "AND is_priority = false AND is_custom = false"
    )
    op.execute("UPDATE checklist_template_items SET is_active = false")


def downgrade() -> None:
    op.execute("UPDATE checklist_template_items SET is_active = true")
