"""university recognition sections

Adds `universities.recognition`: the source document's own sections, kept
verbatim.

The PDF behind this column is not a league table. It is per-institution "why
choose us" copy, and only part of it is a placing. A bullet carrying both a
source and a year already had a home in `rankings`; an award, rating or
accreditation had one in `awards`; a list of graduate employers had one in
`employability`. What is left over is the majority of the document by volume —
Portsmouth's sustainability programme, UCA's partner institutions, Hull's
research consortium, Worcester's alumni-award rules — and it fits none of those
shapes without being flattened into something it is not.

So it is stored as the document writes it: an ordered list of sections, each
with the heading it carried in the source and its bullets beneath. Rendering it
verbatim is the point, and it means nothing in the file is lost to a schema
that was designed for a different kind of claim.

    [{"heading": str, "items": [{"label": str, "detail"?: str, "sub"?: [str]}]}]

Nullable with no default, like every other public-catalogue column on this
table: the public site hides any section whose field is absent, so a university
with no recognition data must produce no key at all rather than an empty list.

Revision ID: d1a7c93e4f20
Revises: b42df9339ebc
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d1a7c93e4f20"
down_revision: Union[str, Sequence[str], None] = "b42df9339ebc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "universities",
        sa.Column("recognition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("universities", "recognition")
