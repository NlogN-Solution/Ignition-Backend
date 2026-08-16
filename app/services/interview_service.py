"""Mock interview practice.

Phase 6. Scoring sits behind `AnswerScorer` so the session lifecycle — start,
answer, complete, get feedback — never changes shape when the stub is replaced
by a real model. `WordCountScorer` is the stub: deterministic and cheap, which
matters for tests, but explicitly not a claim about answer quality.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException, NotFoundException
from ..core.events import InterviewCompleted, event_bus
from ..models import (
    InterviewAnswer,
    InterviewFeedbackBand,
    InterviewSession,
    InterviewType,
    User,
)
from ..models.enums import InterviewSessionStatus

#: Below this many words an answer scores nothing — a one-word response is not
#: a partial attempt, it is a placeholder.
MIN_SCORED_WORDS = 5
#: Word count at which an answer earns full marks. Chosen to reward a complete,
#: structured answer without rewarding padding beyond it.
FULL_SCORE_WORDS = 40


class AnswerScorer(Protocol):
    def score(self, prompt: str, answer_text: str, max_score: int) -> tuple[int, dict[str, object]]:
        """Return (score, notes). `notes` is opaque to the caller — it is
        whatever the scorer wants the student to see alongside the number."""
        ...


class WordCountScorer:
    """Deterministic stand-in for a real scoring model.

    Scores by how much of the answer's ceiling word count was used, linearly
    between `MIN_SCORED_WORDS` and `FULL_SCORE_WORDS`. Crude on purpose: it is
    a placeholder for a model that reads content, not a claim that word count
    measures interview quality.
    """

    def score(self, prompt: str, answer_text: str, max_score: int) -> tuple[int, dict[str, object]]:
        word_count = len(answer_text.split())
        fraction = 0.0 if word_count < MIN_SCORED_WORDS else min(1.0, word_count / FULL_SCORE_WORDS)
        points = round(max_score * fraction)
        return points, {"word_count": word_count, "scorer": "word_count_v1"}


def get_answer_scorer() -> AnswerScorer:
    return WordCountScorer()


class InterviewService:
    def __init__(self, session: AsyncSession, scorer: AnswerScorer | None = None) -> None:
        self.session = session
        self.scorer = scorer or WordCountScorer()

    async def list_types(self) -> list[InterviewType]:
        result = await self.session.scalars(
            select(InterviewType)
            .where(InterviewType.is_active.is_(True))
            .options(selectinload(InterviewType.questions))
            .order_by(InterviewType.name)
        )
        return list(result)

    async def _type_or_404(self, type_id: UUID) -> InterviewType:
        interview_type = await self.session.scalar(
            select(InterviewType)
            .where(InterviewType.id == type_id, InterviewType.is_active.is_(True))
            .options(selectinload(InterviewType.questions))
        )
        if interview_type is None:
            raise NotFoundException("Interview type not found")
        return interview_type

    async def start_session(self, student: User, type_id: UUID) -> InterviewSession:
        interview_type = await self._type_or_404(type_id)
        session_row = InterviewSession(
            student_id=student.id,
            type_id=interview_type.id,
            status=InterviewSessionStatus.IN_PROGRESS,
            started_at=datetime.now(UTC),
        )
        self.session.add(session_row)
        await self.session.commit()
        # Re-fetched with every relationship eager-loaded rather than patched
        # onto the just-created row: `answers` and `feedback_band` still need
        # loading, and doing it through the same query `_session_or_404` uses
        # keeps there being exactly one way this shape gets built.
        return await self._session_or_404(student, session_row.id)

    async def _session_or_404(self, student: User, session_id: UUID) -> InterviewSession:
        session_row = await self.session.scalar(
            select(InterviewSession)
            .where(InterviewSession.id == session_id, InterviewSession.student_id == student.id)
            .options(
                selectinload(InterviewSession.answers),
                selectinload(InterviewSession.type).selectinload(InterviewType.questions),
                selectinload(InterviewSession.feedback_band),
            )
        )
        if session_row is None:
            raise NotFoundException("Interview session not found")
        return session_row

    async def get_session(self, student: User, session_id: UUID) -> InterviewSession:
        return await self._session_or_404(student, session_id)

    async def list_sessions(self, student: User) -> list[InterviewSession]:
        result = await self.session.scalars(
            select(InterviewSession)
            .where(InterviewSession.student_id == student.id)
            .options(selectinload(InterviewSession.type), selectinload(InterviewSession.feedback_band))
            .order_by(InterviewSession.started_at.desc())
        )
        return list(result)

    async def submit_answer(
        self, student: User, session_id: UUID, question_id: UUID, answer_text: str
    ) -> InterviewAnswer:
        """Score and store one answer. Resubmitting replaces the previous score
        — practising the same question again should not stack points onto it."""
        session_row = await self._session_or_404(student, session_id)
        if session_row.is_complete:
            raise BadRequestException("This interview is already complete")

        question = next((q for q in session_row.type.questions if q.id == question_id), None)
        if question is None:
            raise NotFoundException("Question not found for this interview")

        points, notes = self.scorer.score(question.prompt, answer_text, question.max_score)

        existing = await self.session.scalar(
            select(InterviewAnswer).where(
                InterviewAnswer.session_id == session_row.id, InterviewAnswer.question_id == question_id
            )
        )
        if existing is not None:
            existing.answer_text = answer_text
            existing.score = points
            existing.scorer_notes = notes
            answer = existing
        else:
            answer = InterviewAnswer(
                session_id=session_row.id,
                question_id=question_id,
                answer_text=answer_text,
                score=points,
                scorer_notes=notes,
            )
            self.session.add(answer)
        await self.session.commit()
        await self.session.refresh(answer)
        return answer

    async def complete_session(self, student: User, session_id: UUID) -> InterviewSession:
        """Total the answers, match a feedback band, close the session.

        Unanswered questions score zero rather than blocking completion — a
        student who runs out of time should still get feedback on what they did
        answer, not a dead end.
        """
        session_row = await self._session_or_404(student, session_id)
        if session_row.is_complete:
            return session_row

        total = sum(answer.score for answer in session_row.answers)
        band = await self.session.scalar(
            select(InterviewFeedbackBand)
            .where(InterviewFeedbackBand.min_score <= total)
            .order_by(InterviewFeedbackBand.min_score.desc())
            .limit(1)
        )

        session_row.status = InterviewSessionStatus.COMPLETED
        session_row.completed_at = datetime.now(UTC)
        session_row.score = total
        session_row.feedback_band_id = band.id if band else None
        session_row.feedback_band = band
        await self.session.commit()
        # No refresh: every field the caller reads was just set in Python, and
        # refreshing would expire the eager-loaded `type`/`answers`/
        # `feedback_band` relationships, forcing a lazy load outside the async
        # greenlet the next time a route serializes this row.

        await event_bus.publish(
            InterviewCompleted(
                session_id=session_row.id,
                student_id=student.id,
                type_key=session_row.type.key,
                score=total,
            ),
            self.session,
        )
        return session_row


async def get_interview_service(session: AsyncSession = Depends(get_db_session)) -> InterviewService:
    return InterviewService(session)
