"""content_rich_body

Revision ID: e2c4a6f8b913
Revises: d5f81c3a9e27
Create Date: 2026-09-27 18:00:00.000000

Articles and guides are now written in a rich-text editor. The body is stored
as sanitised HTML on the page itself, with a cover image, rather than as prose
blocks. Blocks remain for pages assembled from site components.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2c4a6f8b913"
down_revision: str | Sequence[str] | None = "d5f81c3a9e27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("content_pages", sa.Column("body_html", sa.Text(), nullable=True))
    op.add_column("content_pages", sa.Column("cover_image_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("content_pages", "cover_image_url")
    op.drop_column("content_pages", "body_html")
