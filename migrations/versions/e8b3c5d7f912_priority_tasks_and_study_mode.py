"""priority_tasks_and_study_mode

Two staff-editable facts the student portal shows but no staff screen could set:

- `student_checklist_items.is_priority` / `assigned_by`: tasks a counsellor sets
  for a student, which the student dashboard's "Priority tasks" leads with.
- `applications.study_mode`: this student's mode of study, per application.

Revision ID: e8b3c5d7f912
Revises: d4e7a1c9b2f6
Create Date: 2026-09-19 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b3c5d7f912"
down_revision: str | Sequence[str] | None = "d4e7a1c9b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "student_checklist_items",
        sa.Column("is_priority", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("student_checklist_items", sa.Column("assigned_by", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_student_checklist_items_assigned_by_users",
        "student_checklist_items",
        "users",
        ["assigned_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_student_checklist_items_priority",
        "student_checklist_items",
        ["student_id"],
        postgresql_where=sa.text("is_priority"),
    )
    op.add_column("applications", sa.Column("study_mode", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "study_mode")
    op.drop_index("idx_student_checklist_items_priority", table_name="student_checklist_items")
    op.drop_constraint("fk_student_checklist_items_assigned_by_users", "student_checklist_items", type_="foreignkey")
    op.drop_column("student_checklist_items", "assigned_by")
    op.drop_column("student_checklist_items", "is_priority")
