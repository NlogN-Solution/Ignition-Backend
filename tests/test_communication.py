"""Correspondence, and the discontinuity it exists to fix.

A lead and a student are the same person. Until threads resolved by person
rather than by lifecycle stage, converting a lead produced an empty inbox for
somebody staff had been talking to for weeks — the history was filed under an
identity the student page could not see.

`test_lead_correspondence_survives_conversion` is the one that matters. The
rest guard the authorisation boundaries around it.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

COMMS = "/api/v1/communication"
STUDENT = "/api/v1/student"
LEADS = "/api/v1/leads"


async def _send(client: AsyncClient, thread_id: str, headers: dict, body: str) -> dict:
    """Replies are multipart, always — see the note in schemas/communication.py."""
    response = await client.post(
        f"{COMMS}/threads/{thread_id}/messages",
        data={"body": body},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ── TEST E — the whole point ─────────────────────────────────────────────────


async def test_lead_correspondence_survives_conversion(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """TEST E. Staff talk to a lead; the lead becomes a student; the history
    is still there, from both screens, and the conversation continues in the
    same thread rather than a new one."""
    counsellor = await user_factory(UserRole.COUNSELLOR, email="comms.counsellor@example.com")
    staff_headers = await auth_headers(counsellor)

    lead = (
        await client.post(
            LEADS,
            json={"first_name": "Kabin", "last_name": "Shrestha", "phone": "9800000001"},
            headers=staff_headers,
        )
    ).json()

    # Three weeks of pre-application conversation, compressed.
    thread = (
        await client.post(
            f"{COMMS}/threads",
            json={
                "subject": "Studying computer science in the UK",
                "body": "Thanks for getting in touch — here is what we would need from you.",
                "lead_id": lead["id"],
            },
            headers=staff_headers,
        )
    ).json()
    await _send(client, thread["id"], staff_headers, "Following up on the documents.")

    assert thread["participant"]["stage"] == "lead"

    # The lead converts, which is what used to break everything.
    student = await user_factory(UserRole.STUDENT, email="comms.kabin@example.com")
    converted = await client.post(
        f"{LEADS}/{lead['id']}/convert",
        json={"converted_user_id": str(student.id)},
        headers=staff_headers,
    )
    assert converted.status_code == 200, converted.text

    # 1. The student page shows the pre-conversion history.
    from_student_page = await client.get(
        f"{COMMS}/students/{student.id}/threads", headers=staff_headers
    )
    assert from_student_page.status_code == 200, from_student_page.text
    subjects = [item["subject"] for item in from_student_page.json()]
    assert "Studying computer science in the UK" in subjects, (
        "converting a lead must not empty their correspondence history"
    )

    # 2. The lead page still shows it too — continuity has to work both ways.
    from_lead_page = await client.get(f"{COMMS}/leads/{lead['id']}/threads", headers=staff_headers)
    assert [item["subject"] for item in from_lead_page.json()] == [
        "Studying computer science in the UK"
    ]

    # 3. The student can read it in their own portal.
    student_headers = await auth_headers(student)
    mine = await client.get(f"{STUDENT}/me/threads", headers=student_headers)
    assert mine.status_code == 200, mine.text
    assert [item["subject"] for item in mine.json()] == ["Studying computer science in the UK"]

    # 4. And replying continues the same thread rather than starting a parallel one.
    await _send(client, thread["id"], student_headers, "Thanks — I have my transcripts ready.")
    detail = (await client.get(f"{COMMS}/threads/{thread['id']}", headers=staff_headers)).json()
    assert len(detail["messages"]) == 3
    assert detail["messages"][-1]["is_from_student"] is True
    assert detail["participant"]["stage"] == "student (from lead)", (
        "both ids set is the signal that this conversation crossed the conversion"
    )
    assert len((await client.get(f"{STUDENT}/me/threads", headers=student_headers)).json()) == 1


# ── Authorisation ────────────────────────────────────────────────────────────


async def test_a_student_cannot_read_another_students_thread(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR, email="comms.c2@example.com")
    owner = await user_factory(UserRole.STUDENT, email="comms.owner@example.com")
    stranger = await user_factory(UserRole.STUDENT, email="comms.stranger@example.com")
    staff_headers = await auth_headers(counsellor)

    thread = (
        await client.post(
            f"{COMMS}/threads",
            json={"subject": "Your offer", "body": "Congratulations.", "student_id": str(owner.id)},
            headers=staff_headers,
        )
    ).json()

    # 404 rather than 403: whether a thread exists is itself information about
    # somebody else's correspondence.
    peek = await client.get(f"{COMMS}/threads/{thread['id']}", headers=await auth_headers(stranger))
    assert peek.status_code == 404

    reply = await client.post(
        f"{COMMS}/threads/{thread['id']}/messages",
        data={"body": "let me in"},
        headers=await auth_headers(stranger),
    )
    assert reply.status_code == 404
    assert (await client.get(f"{STUDENT}/me/threads", headers=await auth_headers(stranger))).json() == []


async def test_internal_notes_never_reach_the_student(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """Staff write about a student in the same place they write to them.

    That is only safe because the exclusion is structural — every
    student-facing read goes through one filter rather than remembering a
    `where` clause.
    """
    counsellor = await user_factory(UserRole.COUNSELLOR, email="comms.c3@example.com")
    student = await user_factory(UserRole.STUDENT, email="comms.noted@example.com")
    staff_headers = await auth_headers(counsellor)
    student_headers = await auth_headers(student)

    note = (
        await client.post(
            f"{COMMS}/threads",
            json={
                "subject": "Internal: funding looks thin",
                "body": "Bank statement is short. Do not raise with the student yet.",
                "student_id": str(student.id),
                "visibility": "internal",
            },
            headers=staff_headers,
        )
    ).json()

    assert (await client.get(f"{STUDENT}/me/threads", headers=student_headers)).json() == []
    assert (await client.get(f"{COMMS}/threads/{note['id']}", headers=student_headers)).status_code == 404
    # Staff can still see it.
    assert (await client.get(f"{COMMS}/threads/{note['id']}", headers=staff_headers)).status_code == 200


async def test_a_student_can_open_a_thread_and_staff_see_it(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR, email="comms.c4@example.com")
    student = await user_factory(UserRole.STUDENT, email="comms.asks@example.com")

    created = await client.post(
        f"{STUDENT}/me/threads",
        json={"subject": "Question about my deposit", "body": "When is it due?"},
        headers=await auth_headers(student),
    )
    assert created.status_code == 201, created.text
    assert created.json()["messages"][0]["is_from_student"] is True

    inbox = await client.get(f"{COMMS}/threads", headers=await auth_headers(counsellor))
    assert "Question about my deposit" in [item["subject"] for item in inbox.json()["items"]]


async def test_a_student_cannot_forge_a_staff_message(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """`is_from_student` comes from the caller's role, never the request."""
    student = await user_factory(UserRole.STUDENT, email="comms.forger@example.com")
    headers = await auth_headers(student)
    thread = (
        await client.post(
            f"{STUDENT}/me/threads",
            json={"subject": "Hello", "body": "First"},
            headers=headers,
        )
    ).json()

    message = await _send(client, thread["id"], headers, "Pretending to be staff")
    assert message["is_from_student"] is True


async def test_unread_counts_are_relative_to_who_is_looking(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The student counts unread staff replies; staff count unread student
    messages. One stored flag could not answer both."""
    counsellor = await user_factory(UserRole.COUNSELLOR, email="comms.c5@example.com")
    student = await user_factory(UserRole.STUDENT, email="comms.unread@example.com")
    staff_headers = await auth_headers(counsellor)
    student_headers = await auth_headers(student)

    thread = (
        await client.post(
            f"{COMMS}/threads",
            json={"subject": "Documents", "body": "Please send your passport.", "student_id": str(student.id)},
            headers=staff_headers,
        )
    ).json()

    mine = (await client.get(f"{STUDENT}/me/threads", headers=student_headers)).json()
    assert mine[0]["unread_count"] == 1, "the staff message is unread for the student"

    # Opening it marks the other side's messages read.
    await client.get(f"{COMMS}/threads/{thread['id']}", headers=student_headers)
    mine = (await client.get(f"{STUDENT}/me/threads", headers=student_headers)).json()
    assert mine[0]["unread_count"] == 0

    await _send(client, thread["id"], student_headers, "Attached.")
    staff_view = (await client.get(f"{COMMS}/threads", headers=staff_headers)).json()["items"]
    assert staff_view[0]["unread_count"] == 1, "now it is staff who have something unread"
