"""Mock interview practice.

Phase 6, fourth module. `InterviewType` and `InterviewQuestion` are the catalog
— the same question set for everyone, matching the portal's `interviewTypes.json`
/ `interviews.json` question sets. `InterviewSession` and `InterviewAnswer` are
one student's attempt.

Scoring lives behind `AnswerScorer` in `interview_service.py`, not on these
models: what changes over time is *how* an answer is judged, never the shape of
a session or a question, so the model layer should not need to know a real
scoring engine replaced the word-count stub.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import InterviewSessionStatus

if TYPE_CHECKING:
    from .user import User


class InterviewType(Base, UUIDPKMixin, TimestampMixin):
    """One kind of mock interview — academic, visa, merit-scholarship."""

    __tablename__ = "interview_types"

    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    #: Out of 100 — every type's question `max_score` values are seeded to sum
    #: to 100, so a session's total score is directly comparable across types
    #: and against both this threshold and `InterviewFeedbackBand.min_score`.
    passing_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="70")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    questions: Mapped[list[InterviewQuestion]] = relationship(
        back_populates="type", order_by="InterviewQuestion.order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<InterviewType key={self.key}>"


class InterviewQuestion(Base, UUIDPKMixin, TimestampMixin):
    """One prompt within a type's question set."""

    __tablename__ = "interview_questions"

    type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_types.id", ondelete="CASCADE"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str | None] = mapped_column(Text)
    max_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="25")
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    type: Mapped[InterviewType] = relationship(back_populates="questions")

    __table_args__ = (Index("idx_interview_questions_type_id", "type_id"),)

    def __repr__(self) -> str:
        return f"<InterviewQuestion type_id={self.type_id} order={self.order}>"


class InterviewFeedbackBand(Base, UUIDPKMixin, TimestampMixin):
    """The verdict a session's total score lands in.

    A catalog rather than text generated per session: the portal's four bands
    (`fb-excellent`, `fb-good`, `fb-average`, `fb-weak`) are fixed copy, matched
    by threshold at scoring time.
    """

    __tablename__ = "interview_feedback_bands"

    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    #: The lowest score this band applies to; matching picks the highest
    #: `min_score` the session's total meets or exceeds.
    min_score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(50), nullable=False)
    tone: Mapped[str | None] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[list[str] | None] = mapped_column(JSONB)
    improvements: Mapped[list[str] | None] = mapped_column(JSONB)

    def __repr__(self) -> str:
        return f"<InterviewFeedbackBand key={self.key} min_score={self.min_score}>"


class InterviewSession(Base, UUIDPKMixin, TimestampMixin):
    """One student's attempt at one interview type."""

    __tablename__ = "interview_sessions"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_types.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[InterviewSessionStatus] = mapped_column(
        enum_type(InterviewSessionStatus, "interview_session_status", create_type=False),
        nullable=False,
        server_default=InterviewSessionStatus.IN_PROGRESS.value,
    )
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    #: NULL until scored at completion — a session in progress has no total yet.
    score: Mapped[int | None] = mapped_column(Integer)
    feedback_band_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interview_feedback_bands.id", ondelete="SET NULL")
    )

    student: Mapped[User] = relationship(back_populates="interview_sessions")
    type: Mapped[InterviewType] = relationship()
    feedback_band: Mapped[InterviewFeedbackBand | None] = relationship()
    answers: Mapped[list[InterviewAnswer]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="InterviewAnswer.created_at"
    )

    __table_args__ = (Index("idx_interview_sessions_student_id", "student_id"),)

    @property
    def is_complete(self) -> bool:
        return self.status is InterviewSessionStatus.COMPLETED

    def __repr__(self) -> str:
        return f"<InterviewSession student_id={self.student_id} status={self.status}>"


class InterviewAnswer(Base, UUIDPKMixin, TimestampMixin):
    """One question, answered, scored."""

    __tablename__ = "interview_answers"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_questions.id", ondelete="RESTRICT"), nullable=False
    )
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    #: What the scorer noticed, for the student to read alongside the number —
    #: not structured, because the stub and a future real scorer will not agree
    #: on a schema for it.
    scorer_notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    session: Mapped[InterviewSession] = relationship(back_populates="answers")
    question: Mapped[InterviewQuestion] = relationship()

    __table_args__ = (
        Index("idx_interview_answers_session_id", "session_id"),
        # One answer per question per session — resubmitting replaces it rather
        # than creating a second row for the same prompt.
        Index("uq_interview_answers_session_question", "session_id", "question_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<InterviewAnswer session_id={self.session_id} question_id={self.question_id} score={self.score}>"
