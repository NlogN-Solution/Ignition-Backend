"""document_type_cas_letter

Revision ID: f3c81ba4d072
Revises: e2f5a71c3d90
Create Date: 2026-09-15 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "f3c81ba4d072"
down_revision: Union[str, Sequence[str], None] = "e2f5a71c3d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hand-written for the same reason as e2f5a71c3d90 and b987163a5ae1:
    # autogenerate never detects enum *value* additions to an existing
    # Postgres type.
    #
    # `cas_received` has been an ApplicationStatus since e2f5a71c3d90, but the
    # letter that status is named after had nowhere to be filed — staff picked
    # "Other" and the student's portal could not tell it apart from a bank
    # statement. This gives it a type of its own.
    op.execute("ALTER TYPE document_type ADD VALUE IF NOT EXISTS 'cas_letter'")


def downgrade() -> None:
    # Postgres has no DROP VALUE, so the type is recreated without the label and
    # the columns using it are repointed. The CAST fails loudly if any row has
    # actually reached 'cas_letter' — a downgrade must never silently rewrite a
    # real document's type.
    op.execute("ALTER TYPE document_type RENAME TO document_type_old")
    op.execute(
        "CREATE TYPE document_type AS ENUM ("
        "'passport', 'citizenship', 'national_id', 'academic_transcript', "
        "'academic_certificate', 'provisional_certificate', 'character_certificate', "
        "'english_test', 'cv', 'statement_of_purpose', 'recommendation_letter', "
        "'offer_letter', 'visa', 'financial_document', 'medical_report', 'photo', 'other')"
    )
    for table, column in (
        ("documents", "document_type"),
        ("workflow_stage_document_requirements", "document_type"),
        ("application_checklist_items", "document_type"),
    ):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE document_type "
            f"USING {column}::text::document_type"
        )
    op.execute("DROP TYPE document_type_old")
