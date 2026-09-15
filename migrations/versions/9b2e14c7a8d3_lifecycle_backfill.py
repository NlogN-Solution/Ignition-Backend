"""lifecycle backfill: messages into threads, and the access fee price list

Revision ID: 9b2e14c7a8d3
Revises: 405db07a9f6f
Create Date: 2026-09-15 12:00:00.000000

Data, not schema. Split from 405db07a9f6f deliberately: a structural migration
that also moves rows is two failures wearing one revision id, and this half is
the one worth being able to re-run and reason about on its own.

## 1. Existing correspondence

`messages` was one flat conversation per student. `message_threads` +
`thread_messages` replace it. Every existing message is moved into a thread so
no correspondence disappears at the cut-over — a student who wrote in last week
must still see their own message, and the counsellor must still see the reply.

The old `messages` table is **left in place and untouched**. Dropping it in the
same change that replaces it means a rollback loses whatever arrived in
between, and there is no cost to keeping 2 rows of history around until the new
path has been running long enough to trust. Removing it is a later, separate,
boring migration.

## 2. The access fee price list

`portal_access_fees` was empty in production. `PortalAccessService.fee_for`
returns None for an empty list, checkout refuses with "No access fee is
configured for your country yet", and the paywall is therefore unbuyable — the
feature has been dark since it was written. This seeds the fallback row
(NPR 5,000), which is the price every student is quoted until a country-specific
row is added above it.

Idempotent: seeds only when there is no fallback row, so re-running cannot
duplicate the price list or overwrite a price staff have since edited.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9b2e14c7a8d3"
down_revision: Union[str, Sequence[str], None] = "405db07a9f6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: What a student is quoted when their country has no price of its own.
DEFAULT_ACCESS_FEE = 5000
DEFAULT_ACCESS_CURRENCY = "NPR"


def upgrade() -> None:
    connection = op.get_bind()

    # ── 1. One thread per student who has any message history ────────────────
    #
    # Subject is fixed rather than invented from the first message's text: the
    # old table had no subject, and a truncated first line masquerading as one
    # reads like data that was always there. "Support" is honest about what
    # these rows are.
    connection.execute(
        sa.text(
            """
            INSERT INTO message_threads
                (id, subject, student_id, visibility, created_by,
                 last_message_at, last_message_preview, is_closed, created_at, updated_at)
            SELECT
                gen_random_uuid(),
                'Support',
                m.student_id,
                'shared',
                NULL,
                MAX(m.created_at),
                left(
                    (ARRAY_AGG(m.body ORDER BY m.created_at DESC))[1],
                    300
                ),
                false,
                MIN(m.created_at),
                MAX(m.created_at)
            FROM messages m
            WHERE NOT EXISTS (
                SELECT 1 FROM message_threads t WHERE t.student_id = m.student_id
            )
            GROUP BY m.student_id
            """
        )
    )

    # ── 2. Move the messages in, preserving author, direction and read state ──
    connection.execute(
        sa.text(
            """
            INSERT INTO thread_messages
                (id, thread_id, author_id, author_name, is_from_student,
                 body, body_html, read_at, created_at)
            SELECT
                m.id,
                t.id,
                m.sender_id,
                NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''),
                m.is_from_student,
                m.body,
                NULL,
                CASE WHEN m.is_read THEN m.created_at ELSE NULL END,
                m.created_at
            FROM messages m
            JOIN message_threads t ON t.student_id = m.student_id
            LEFT JOIN users u ON u.id = m.sender_id
            WHERE NOT EXISTS (
                SELECT 1 FROM thread_messages tm WHERE tm.id = m.id
            )
            """
        )
    )

    # ── 3. The access fee fallback ───────────────────────────────────────────
    connection.execute(
        sa.text(
            """
            INSERT INTO portal_access_fees (id, country_id, amount, currency, is_active, notes, created_at, updated_at)
            SELECT gen_random_uuid(), NULL, :amount, :currency, true,
                   'Default one-time portal access fee. Add a country row above this to override it.',
                   now(), now()
            WHERE NOT EXISTS (SELECT 1 FROM portal_access_fees WHERE country_id IS NULL)
            """
        ),
        {"amount": DEFAULT_ACCESS_FEE, "currency": DEFAULT_ACCESS_CURRENCY},
    )


def downgrade() -> None:
    connection = op.get_bind()

    # Only the rows this migration created, identified by the ids it copied.
    # A reply written *through the new UI* has no counterpart in `messages` and
    # must survive a rollback — deleting the whole table would throw away
    # correspondence that only ever existed here.
    connection.execute(
        sa.text("DELETE FROM thread_messages WHERE id IN (SELECT id FROM messages)")
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM message_threads t
            WHERE NOT EXISTS (SELECT 1 FROM thread_messages m WHERE m.thread_id = t.id)
              AND t.subject = 'Support'
            """
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM portal_access_fees WHERE country_id IS NULL AND amount = :amount AND currency = :currency"
        ),
        {"amount": DEFAULT_ACCESS_FEE, "currency": DEFAULT_ACCESS_CURRENCY},
    )
