"""security_hardening

Schema half of the 2026-09 security remediation.

1. **Case-insensitive e-mail uniqueness (FAPI-SEC-009).** `users.email` was
   unique byte-for-byte, so `A@x.com` and `a@x.com` could both register.
   Existing addresses are lower-cased and a unique index on `lower(email)` is
   added. If two accounts already differ only by case, the migration stops
   and says so rather than choosing which person keeps the address — that is a
   decision for a human. Find them with:

       SELECT lower(email), array_agg(id) FROM users
       GROUP BY lower(email) HAVING count(*) > 1;

2. **Private leave attachments (FAPI-SEC-006).** Medical notes were uploaded
   as public Cloudinary assets and the row held their public URL. New uploads
   are private and the row holds the stored name, served only through an
   authenticated, owner-or-manager route. Existing public assets are moved by
   `scripts/migrate_leave_attachments_private.py`, which needs Cloudinary
   credentials and so is not run from here.

Revision ID: b4e1d9c2a7f3
Revises: a1b2c3d4e5f6
Create Date: 2026-09-26 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e1d9c2a7f3"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text("SELECT count(*) FROM (SELECT lower(email) FROM users GROUP BY lower(email) HAVING count(*) > 1) d")
    ).scalar_one()
    if duplicates:
        raise RuntimeError(
            f"{duplicates} e-mail address(es) are held by more than one account when compared "
            "case-insensitively. Merge or rename those accounts, then re-run the migration. "
            "See this migration's docstring for the query that lists them."
        )

    op.execute("UPDATE users SET email = lower(email) WHERE email <> lower(email)")
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.add_column("leave_requests", sa.Column("attachment_stored_file_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("leave_requests", "attachment_stored_file_name")
    op.drop_index("uq_users_email_lower", table_name="users")
