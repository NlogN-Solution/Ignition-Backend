"""portal_access_fee

Ignition charges a one-time fee for access to the student portal, priced per
country. Two changes carry that:

  * `payments.purpose` — access is granted by the presence of a completed
    payment whose purpose is `portal_access`, so the kind of payment has to be
    queryable rather than implied by a free-text remark.
  * `portal_access_fees` — the price list. The row with a NULL `country_id` is
    the fallback for any country without its own price.

Existing payment rows predate the distinction and become `other`, which is
correct: none of them granted portal access.

Revision ID: a1c7e93b52d4
Revises: 7585f25a488b
Create Date: 2026-08-24 12:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c7e93b52d4"
down_revision: Union[str, Sequence[str], None] = "7585f25a488b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PURPOSES = ("portal_access", "application_fee", "tuition_deposit", "other")


def upgrade() -> None:
    payment_purpose = sa.Enum(*PURPOSES, name="payment_purpose")
    payment_purpose.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "payments",
        sa.Column(
            "purpose",
            payment_purpose,
            server_default="other",
            nullable=False,
        ),
    )
    op.create_index(
        "idx_payments_student_purpose_status",
        "payments",
        ["student_id", "purpose", "status"],
    )

    op.create_table(
        "portal_access_fees",
        sa.Column("country_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name=op.f("fk_portal_access_fees_country_id_countries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portal_access_fees")),
        sa.UniqueConstraint("country_id", name=op.f("uq_portal_access_fees_country_id")),
    )


def downgrade() -> None:
    op.drop_table("portal_access_fees")
    op.drop_index("idx_payments_student_purpose_status", table_name="payments")
    op.drop_column("payments", "purpose")
    sa.Enum(name="payment_purpose").drop(op.get_bind(), checkfirst=True)
