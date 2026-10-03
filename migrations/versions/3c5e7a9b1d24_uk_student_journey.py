"""uk_student_journey

The UK student journey: ten stages from application to visa, each with a kind
that decides what the student and staff do there.

- `workflow_stages` gains `kind` and `config`; requirements gain `condition`
  (`ug` / `pg` / `gap`); steps gain `progress` (checklist ticks).
- `applications.has_study_gap`: the student's "gap over six months" answer.
- Two tables: `workflow_step_submissions` (review rounds) and
  `workflow_step_slots` (interview slots, bookings and outcomes).
- Enum values: three document types, two appointment types, six workflow
  activity types.
- Seeds the `uk-student-journey` template, bound to the GB country when it
  exists. The stage data is a frozen copy of `app/services/journey_templates.py`
  as it stood when this was written. Existing applications keep the workflow
  they already have; moving one is a deliberate staff action.

Revision ID: 3c5e7a9b1d24
Revises: f9a2c4e6b810
Create Date: 2026-10-02 12:00:00.000000

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3c5e7a9b1d24"
down_revision: str | Sequence[str] | None = "f9a2c4e6b810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_ENUM_VALUES = {
    "document_type": ("medium_of_instruction", "gap_explanation", "interview_recording"),
    "appointment_type": ("university_mock_interview", "suitability_interview"),
    "workflow_activity_type": (
        "submission",
        "review",
        "slots_published",
        "slot_booked",
        "outcome",
        "task_ticked",
    ),
}

#: Created by `op.create_table` below; dropped by hand in `downgrade`.
_NEW_ENUM_TYPES = ("step_submission_status", "step_slot_status", "step_slot_outcome")

UK_JOURNEY_SLUG = "uk-student-journey"


UK_JOURNEY_STAGES = [
    {
        "key": "application",
        "name": "Application",
        "kind": "documents",
        "description": "Upload the documents the university needs for your application.",
        "config": {"on_complete_status": "ready_to_submit", "level_aware": True},
        "documents": [
            ("academic_certificate", "Class 10 academic documents", None),
            ("academic_certificate", "+2 academic documents", None),
            ("academic_transcript", "Bachelor's transcripts", "pg"),
            ("provisional_certificate", "Bachelor's degree / provisional certificate", "pg"),
            ("recommendation_letter", "Letter of Recommendation (LOR)", None),
            ("medium_of_instruction", "Medium of Instruction (MOI)", None),
            ("passport", "Passport", None),
            ("cv", "CV", None),
            ("statement_of_purpose", "Statement of Purpose (SOP)", None),
            ("gap_explanation", "Gap explanation & evidence (gap > 6 months)", "gap"),
        ],
    },
    {
        "key": "offer",
        "name": "Offer Letter",
        "kind": "issued",
        "description": "The university reviews your application and issues an offer.",
        "config": {"milestone_status": "offer_received"},
        "documents": [],
    },
    {
        "key": "interview_prep",
        "name": "Interview Preparation",
        "kind": "review",
        "description": "Use the resources, prepare your answers and send them to your counsellor.",
        "config": {
            "resources": [
                {
                    "title": "Common interview questions",
                    "description": "Top 40 credibility questions with tips",
                    "url": "",
                },
                {
                    "title": "Course & university research",
                    "description": "Template: why this course, why this university",
                    "url": "",
                },
                {
                    "title": "Finance & sponsor explanation",
                    "description": "How to talk about your funding clearly",
                    "url": "",
                },
                {
                    "title": "Practise with a mock interview",
                    "description": "Scored practice in your portal",
                    "url": "/interviews",
                },
            ],
            "allow_text": True,
            "allow_document": True,
            "allow_link": False,
            "accept": "document",
        },
        "documents": [],
    },
    {
        "key": "interview_recording",
        "name": "Interview Recording",
        "kind": "review",
        "description": "Record yourself answering the practice questions and hand in the video.",
        "config": {"allow_text": False, "allow_document": True, "allow_link": True, "accept": "video"},
        "documents": [],
    },
    {
        "key": "mock_interview",
        "name": "Mock with University",
        "kind": "booking",
        "description": "Pick a slot for your mock interview with the university.",
        "config": {
            "allow_reschedule": True,
            "fail_ends_journey": False,
            "appointment_type": "university_mock_interview",
        },
        "documents": [],
    },
    {
        "key": "suitability_interview",
        "name": "Suitability Interview",
        "kind": "booking",
        "description": "Book your final suitability interview with the university.",
        "config": {
            "allow_reschedule": False,
            "fail_ends_journey": True,
            "on_fail_status": "rejected",
            "appointment_type": "suitability_interview",
        },
        "documents": [],
    },
    {
        "key": "cas_documents",
        "name": "CAS Documents",
        "kind": "documents",
        "description": "Upload the documents the university needs to prepare your CAS.",
        "config": {},
        "documents": [
            ("financial_document", "Financial evidence (bank statement)", None),
            ("financial_document", "Tuition fee deposit receipt", None),
            ("financial_document", "Sponsor & relationship documents", None),
        ],
    },
    {
        "key": "cas_shield",
        "name": "CAS Shield",
        "kind": "documents",
        "description": "Upload the CAS Shield documents for the financial compliance checks.",
        "config": {},
        "documents": [
            ("financial_document", "Source of funds documents", None),
            ("financial_document", "Income / tax documents", None),
            ("financial_document", "Education loan sanction letter", None),
        ],
    },
    {
        "key": "cas_issued",
        "name": "CAS Issued",
        "kind": "issued",
        "description": "The university reviews your CAS documents and issues your CAS.",
        "config": {"milestone_status": "cas_received"},
        "documents": [],
    },
    {
        "key": "visa",
        "name": "Visa",
        "kind": "checklist",
        "description": "Complete each step of your student visa application.",
        "config": {
            "tasks": [
                {"key": "ihs", "label": "Pay Immigration Health Surcharge (IHS)"},
                {"key": "online_form", "label": "Complete online visa application"},
                {"key": "book_biometrics", "label": "Book biometrics appointment"},
                {"key": "upload_documents", "label": "Upload supporting documents"},
                {"key": "attend_biometrics", "label": "Attend biometrics"},
            ],
            "on_complete_status": "visa_processing",
        },
        "documents": [],
    },
]


def upgrade() -> None:
    # Committed before anything else: the seed below writes two of the new
    # document types, and Postgres refuses a value added in the same transaction.
    with op.get_context().autocommit_block():
        for type_name, values in _NEW_ENUM_VALUES.items():
            for value in values:
                op.execute(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column("workflow_stages", sa.Column("kind", sa.String(length=20), server_default="info", nullable=False))
    op.add_column(
        "workflow_stages",
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )
    op.add_column("workflow_stage_document_requirements", sa.Column("condition", sa.String(length=20), nullable=True))
    op.add_column(
        "application_workflow_steps",
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )
    op.add_column("applications", sa.Column("has_study_gap", sa.Boolean(), server_default="false", nullable=False))

    op.create_table(
        "workflow_step_submissions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=False),
        sa.Column("round", sa.Integer(), server_default="1", nullable=False),
        sa.Column("submitted_by", sa.UUID(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("submitted", "verified", "changes_requested", name="step_submission_status"),
            server_default="submitted",
            nullable=False,
        ),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("feedback_document_ids", postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["step_id"], ["application_workflow_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id", "round", name="uq_workflow_step_submissions_step_id_round"),
    )
    op.create_index("idx_workflow_step_submissions_step_id", "workflow_step_submissions", ["step_id"])
    op.create_index("idx_workflow_step_submissions_status", "workflow_step_submissions", ["status"])

    op.create_table(
        "workflow_step_slots",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("meeting_link", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "booked", "withdrawn", "completed", name="step_slot_status"),
            server_default="open",
            nullable=False,
        ),
        sa.Column("outcome", sa.Enum("passed", "reschedule", "failed", name="step_slot_outcome"), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("appointment_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("booked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["step_id"], ["application_workflow_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_workflow_step_slots_step_id", "workflow_step_slots", ["step_id"])
    op.create_index("idx_workflow_step_slots_status", "workflow_step_slots", ["status"])

    _seed_uk_journey()


def _seed_uk_journey() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM workflow_templates WHERE slug = :slug"), {"slug": UK_JOURNEY_SLUG}).first():
        return

    country_id = bind.execute(sa.text("SELECT id FROM countries WHERE iso2 = 'GB'")).scalar()
    template_id = bind.execute(
        sa.text(
            """
            INSERT INTO workflow_templates (name, slug, description, country_id, is_default, is_active)
            VALUES (:name, :slug, :description, :country_id, false, true)
            RETURNING id
            """
        ),
        {
            "name": "UK Student Journey",
            "slug": UK_JOURNEY_SLUG,
            "description": "Application to visa for UK universities: offer, interviews, CAS and visa.",
            "country_id": country_id,
        },
    ).scalar()

    for order, stage in enumerate(UK_JOURNEY_STAGES):
        stage_id = bind.execute(
            sa.text(
                """
                INSERT INTO workflow_stages (template_id, key, name, description, kind, config, "order")
                VALUES (:template_id, :key, :name, :description, :kind, CAST(:config AS jsonb), :order)
                RETURNING id
                """
            ),
            {
                "template_id": template_id,
                "key": stage["key"],
                "name": stage["name"],
                "description": stage["description"],
                "kind": stage["kind"],
                "config": json.dumps(stage["config"]),
                "order": order,
            },
        ).scalar()
        for document_type, label, condition in stage["documents"]:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO workflow_stage_document_requirements
                        (stage_id, document_type, custom_label, is_required, condition)
                    VALUES (:stage_id, CAST(:document_type AS document_type), :label, true, :condition)
                    """
                ),
                {"stage_id": stage_id, "document_type": document_type, "label": label, "condition": condition},
            )


def downgrade() -> None:
    # The template goes only if nothing was started from it; a workflow that
    # references it (ON DELETE RESTRICT) means people are mid-journey.
    op.execute(
        """
        DELETE FROM workflow_templates t
         WHERE t.slug = 'uk-student-journey'
           AND NOT EXISTS (SELECT 1 FROM application_workflows w WHERE w.template_id = t.id)
        """
    )

    op.drop_index("idx_workflow_step_slots_status", table_name="workflow_step_slots")
    op.drop_index("idx_workflow_step_slots_step_id", table_name="workflow_step_slots")
    op.drop_table("workflow_step_slots")
    op.drop_index("idx_workflow_step_submissions_status", table_name="workflow_step_submissions")
    op.drop_index("idx_workflow_step_submissions_step_id", table_name="workflow_step_submissions")
    op.drop_table("workflow_step_submissions")
    for type_name in _NEW_ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {type_name}")

    op.drop_column("applications", "has_study_gap")
    op.drop_column("application_workflow_steps", "progress")
    op.drop_column("workflow_stage_document_requirements", "condition")
    op.drop_column("workflow_stages", "config")
    op.drop_column("workflow_stages", "kind")
    # Enum values cannot be removed from a Postgres type; the added ones stay,
    # unused, which is harmless.
