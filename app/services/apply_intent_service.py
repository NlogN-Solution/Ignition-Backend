"""Minting, reading and claiming an apply intent.

One module so there is exactly one place that turns a slug from the browser
into a programme row. Every other consumer — the public mint endpoint, the
unauthenticated preview the registration screen reads, the claim the portal
makes after sign-in — goes through here and receives an already-resolved
record. A caller cannot accidentally trust the client's course.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import ApplyIntent, Intake, Program
from ..models.apply_intent import INTENT_TTL


class ApplyIntentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    _OPTIONS = (
        selectinload(ApplyIntent.program).selectinload(Program.university),
        selectinload(ApplyIntent.intake),
    )

    async def mint(
        self,
        course_slug: str,
        *,
        intake_id: uuid.UUID | None = None,
        source_path: str | None = None,
    ) -> ApplyIntent | None:
        """Resolve a public course slug and record the intent.

        Returns None when the slug names nothing published — the caller turns
        that into a 404 rather than minting an intent for a course that is not
        on sale. This is the *only* point where a client-supplied course
        identifier is accepted, and it is accepted as a lookup key, never as
        data: the programme's university and every field the "you're applying
        for" card shows are read from the row, not from the request.
        """
        program = await self.session.scalar(
            select(Program)
            .options(selectinload(Program.university))
            .where(Program.slug == course_slug, Program.is_active.is_(True))
        )
        if program is None:
            return None

        # An intake must belong to the programme it is claimed for. Without
        # this check a caller could pin any intake id to any course.
        resolved_intake_id: uuid.UUID | None = None
        if intake_id is not None:
            resolved_intake_id = await self.session.scalar(
                select(Intake.id).where(Intake.id == intake_id, Intake.program_id == program.id)
            )

        intent = ApplyIntent(
            program_id=program.id,
            university_id=program.university_id,
            intake_id=resolved_intake_id,
            # Clamped: this is free text from a browser and it is only ever
            # shown back to staff.
            source_path=(source_path or "")[:300] or None,
        )
        self.session.add(intent)
        await self.session.commit()
        return await self.get(intent.id)

    async def get(self, intent_id: uuid.UUID) -> ApplyIntent | None:
        return await self.session.scalar(
            select(ApplyIntent).options(*self._OPTIONS).where(ApplyIntent.id == intent_id)
        )

    async def get_live(self, intent_id: uuid.UUID) -> ApplyIntent | None:
        """As `get`, but None for an intent that has expired unclaimed."""
        intent = await self.get(intent_id)
        if intent is None:
            return None
        if intent.claimed_at is None and datetime.now(UTC) - intent.created_at > INTENT_TTL:
            return None
        return intent

    async def claim(self, intent_id: uuid.UUID, user_id: uuid.UUID) -> ApplyIntent | None:
        """Bind an intent to whoever just signed in or signed up.

        This is what carries the course across the register-versus-login fork:
        the public site does not know and does not care which door the student
        will use, and neither does this. Claiming is idempotent for the same
        user — the portal may call it on a page it re-renders — and refuses for
        a *different* user, so a shared link cannot re-point somebody else's
        intent at the person who opened it second.
        """
        intent = await self.get_live(intent_id)
        if intent is None:
            return None
        if intent.claimed_by is not None and intent.claimed_by != user_id:
            return None

        if intent.claimed_by is None:
            intent.claimed_by = user_id
            intent.claimed_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(intent)
        return intent

    async def pending_for(self, user_id: uuid.UUID) -> ApplyIntent | None:
        """The course this student is here to apply for, if any.

        The most recent claimed-but-unfulfilled intent. Onboarding reads this
        to show "you're applying for" instead of asking the student to pick a
        course they already picked.
        """
        return await self.session.scalar(
            select(ApplyIntent)
            .options(*self._OPTIONS)
            .where(
                ApplyIntent.claimed_by == user_id,
                ApplyIntent.fulfilled_at.is_(None),
            )
            .order_by(ApplyIntent.claimed_at.desc())
            .limit(1)
        )

    async def mark_fulfilled(self, user_id: uuid.UUID, program_id: uuid.UUID) -> None:
        """Called when an application is actually opened for the course.

        Keyed on (user, programme) rather than on the intent id because the
        application may be started from anywhere — the apply flow, Explore, a
        counsellor — and the intent should stop following the student around
        once they have acted on it however they got there.
        """
        intents = await self.session.scalars(
            select(ApplyIntent).where(
                ApplyIntent.claimed_by == user_id,
                ApplyIntent.program_id == program_id,
                ApplyIntent.fulfilled_at.is_(None),
            )
        )
        touched = False
        for intent in intents:
            intent.fulfilled_at = datetime.now(UTC)
            touched = True
        if touched:
            await self.session.commit()


async def get_apply_intent_service(session: AsyncSession = Depends(get_db_session)) -> ApplyIntentService:
    return ApplyIntentService(session)
