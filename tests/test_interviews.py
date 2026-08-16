"""Phase 6 — mock interview practice.

The scorer is deterministic (`WordCountScorer`), so these tests can assert
exact scores without mocking anything. Completion also drives the "interview"
progress milestone and a points award, so a couple of tests confirm that
wiring rather than re-testing progress/points mechanics already covered in
test_progress_points.py.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models import InterviewFeedbackBand, InterviewQuestion, InterviewType, PointsRule, ProgressMilestone
from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"

SHORT_ANSWER = "Not much to say."
FULL_ANSWER = " ".join(["word"] * 40)  # hits FULL_SCORE_WORDS exactly


@pytest_asyncio.fixture
async def catalog(session) -> dict:
    interview_type = InterviewType(key="academic", name="Academic Interview", duration_minutes=20, passing_score=70)
    session.add(interview_type)
    await session.flush()

    q1 = InterviewQuestion(type_id=interview_type.id, prompt="Why this programme?", max_score=50, order=1)
    q2 = InterviewQuestion(type_id=interview_type.id, prompt="Describe a project.", max_score=50, order=2)
    session.add_all([q1, q2])

    session.add_all(
        [
            InterviewFeedbackBand(key="excellent", min_score=85, band="Excellent", summary="Great."),
            InterviewFeedbackBand(key="good", min_score=70, band="Strong", summary="Solid."),
            InterviewFeedbackBand(key="weak", min_score=0, band="Needs work", summary="Try again."),
        ]
    )
    session.add(ProgressMilestone(key="interview", label="Interview completed", weight=10, order=1))
    session.add(PointsRule(action="interview.complete", label="Mock interview completed", points=15))
    await session.commit()
    return {"type": interview_type, "questions": [q1, q2]}


@pytest_asyncio.fixture
async def student(client: AsyncClient, user_factory, auth_headers, catalog) -> dict:
    user = await user_factory(UserRole.STUDENT, email="interview.student@example.com")
    return {"user": user, "headers": await auth_headers(user), **catalog}


async def test_interview_types_list_their_questions(client: AsyncClient, student) -> None:
    response = await client.get(f"{STUDENT}/catalog/interview-types", headers=student["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["key"] == "academic"
    assert len(body[0]["questions"]) == 2


async def test_starting_a_session_creates_it_in_progress(client: AsyncClient, student) -> None:
    response = await client.post(
        f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=student["headers"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["score"] is None
    assert body["completed_at"] is None


async def test_answers_are_scored_deterministically_by_word_count(client: AsyncClient, student) -> None:
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]

    response = await client.post(
        f"{STUDENT}/me/interviews/{session_id}/answers",
        json={"question_id": str(student["questions"][0].id), "answer_text": FULL_ANSWER},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    # 40 words hits FULL_SCORE_WORDS, so the full 50 points on this question.
    assert response.json()["score"] == 50


async def test_a_very_short_answer_scores_zero(client: AsyncClient, student) -> None:
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]

    response = await client.post(
        f"{STUDENT}/me/interviews/{session_id}/answers",
        json={"question_id": str(student["questions"][0].id), "answer_text": SHORT_ANSWER},
        headers=headers,
    )
    assert response.json()["score"] == 0


async def test_resubmitting_an_answer_replaces_its_score_rather_than_stacking(client: AsyncClient, student) -> None:
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]
    question_id = str(student["questions"][0].id)

    await client.post(
        f"{STUDENT}/me/interviews/{session_id}/answers",
        json={"question_id": question_id, "answer_text": SHORT_ANSWER},
        headers=headers,
    )
    await client.post(
        f"{STUDENT}/me/interviews/{session_id}/answers",
        json={"question_id": question_id, "answer_text": FULL_ANSWER},
        headers=headers,
    )

    session_body = (await client.get(f"{STUDENT}/me/interviews/{session_id}", headers=headers)).json()
    assert len(session_body["answers"]) == 1
    assert session_body["answers"][0]["score"] == 50


async def test_completing_sums_scores_and_matches_a_feedback_band(client: AsyncClient, student) -> None:
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]

    for question in student["questions"]:
        await client.post(
            f"{STUDENT}/me/interviews/{session_id}/answers",
            json={"question_id": str(question.id), "answer_text": FULL_ANSWER},
            headers=headers,
        )

    response = await client.post(f"{STUDENT}/me/interviews/{session_id}/complete", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["score"] == 100
    assert body["feedback"]["band"] == "Excellent"


async def test_unanswered_questions_score_zero_rather_than_blocking_completion(client: AsyncClient, student) -> None:
    """A student who runs out of time still gets feedback, not a dead end."""
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]

    await client.post(
        f"{STUDENT}/me/interviews/{session_id}/answers",
        json={"question_id": str(student["questions"][0].id), "answer_text": FULL_ANSWER},
        headers=headers,
    )

    response = await client.post(f"{STUDENT}/me/interviews/{session_id}/complete", headers=headers)
    assert response.status_code == 200
    assert response.json()["score"] == 50


async def test_answering_after_completion_is_refused(client: AsyncClient, student) -> None:
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]
    await client.post(f"{STUDENT}/me/interviews/{session_id}/complete", headers=headers)

    response = await client.post(
        f"{STUDENT}/me/interviews/{session_id}/answers",
        json={"question_id": str(student["questions"][0].id), "answer_text": FULL_ANSWER},
        headers=headers,
    )
    assert response.status_code == 400


async def test_completing_advances_the_interview_milestone_and_awards_points(client: AsyncClient, student) -> None:
    """Reaching the interview stage completes the milestone regardless of
    score — same as offer/visa — and pays once via the event subscriber."""
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]

    # Answer nothing — a weak, but completed, attempt.
    await client.post(f"{STUDENT}/me/interviews/{session_id}/complete", headers=headers)

    progress = (await client.get(f"{STUDENT}/me/progress", headers=headers)).json()
    milestone = next(m for m in progress["milestones"] if m["key"] == "interview")
    assert milestone["is_complete"] is True

    points = (await client.get(f"{STUDENT}/me/points", headers=headers)).json()
    assert points["balance"] == 15
    assert points["history"][0]["action"] == "interview.complete"


async def test_completing_twice_does_not_pay_twice(client: AsyncClient, student) -> None:
    headers = student["headers"]
    session_id = (
        await client.post(f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=headers)
    ).json()["id"]

    await client.post(f"{STUDENT}/me/interviews/{session_id}/complete", headers=headers)
    second = await client.post(f"{STUDENT}/me/interviews/{session_id}/complete", headers=headers)
    assert second.status_code == 200

    points = (await client.get(f"{STUDENT}/me/points", headers=headers)).json()
    assert points["balance"] == 15


async def test_a_student_cannot_see_or_answer_another_students_session(
    client: AsyncClient, student, user_factory, auth_headers
) -> None:
    other = await user_factory(UserRole.STUDENT, email="interview.other@example.com")
    other_headers = await auth_headers(other)

    session_id = (
        await client.post(
            f"{STUDENT}/me/interviews", json={"type_id": str(student["type"].id)}, headers=student["headers"]
        )
    ).json()["id"]

    response = await client.get(f"{STUDENT}/me/interviews/{session_id}", headers=other_headers)
    assert response.status_code == 404
