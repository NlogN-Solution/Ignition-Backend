from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InterviewQuestionRead(BaseModel):
    id: UUID
    prompt: str
    hint: str | None = None
    max_score: int
    order: int

    model_config = ConfigDict(from_attributes=True)


class InterviewTypeRead(BaseModel):
    id: UUID
    key: str
    name: str
    description: str | None = None
    duration_minutes: int
    passing_score: int
    questions: list[InterviewQuestionRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class InterviewFeedbackBandRead(BaseModel):
    key: str
    band: str
    tone: str | None = None
    summary: str
    strengths: list[str] | None = None
    improvements: list[str] | None = None

    model_config = ConfigDict(from_attributes=True)


class InterviewAnswerRead(BaseModel):
    id: UUID
    question_id: UUID
    answer_text: str
    score: int

    model_config = ConfigDict(from_attributes=True)


class InterviewSessionCreate(BaseModel):
    type_id: UUID


class InterviewAnswerCreate(BaseModel):
    question_id: UUID
    answer_text: str = Field(min_length=1)


class InterviewSessionRead(BaseModel):
    id: UUID
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    score: int | None = None
    type: InterviewTypeRead
    answers: list[InterviewAnswerRead] = Field(default_factory=list)
    feedback: InterviewFeedbackBandRead | None = Field(default=None, validation_alias="feedback_band")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class InterviewSessionSummary(BaseModel):
    """The list view — no questions or answers, just what happened.

    Built explicitly by the route rather than `model_validate`'d off the ORM
    row: `type_name` and `feedback_band` are strings pulled off nested related
    objects, and letting pydantic alias into a relationship would hand back
    the related object, not its name.
    """

    id: UUID
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    score: int | None = None
    type_name: str
    feedback_band: str | None = None


class InterviewSessionList(BaseModel):
    items: list[InterviewSessionSummary]
    total: int
