"""Appointments, payments, tasks and notifications.

Each of these had a write endpoint guarded by a bare `get_current_user` in
ED360, or a server-controlled audit field accepted from the request body.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

APPOINTMENTS = "/api/v1/appointments"
PAYMENTS = "/api/v1/payments"
TASKS = "/api/v1/tasks"
NOTIFICATIONS = "/api/v1/notifications"


# ── Appointments ──────────────────────────────────────────────────────────────


async def test_a_student_requests_rather_than_schedules(client: AsyncClient, user_factory, auth_headers) -> None:
    """A student may ask for a slot; they may not book one. Every scheduling
    field they send is overwritten."""
    student = await user_factory(UserRole.STUDENT)

    response = await client.post(
        APPOINTMENTS,
        json={
            "appointment_type": "consultation",
            "title": "Course advice",
            "preferred_date": "2027-03-01",
            "start_time": "2027-03-01T09:00:00Z",
            "end_time": "2027-03-01T10:00:00Z",
            "location": "Room 1",
            "status": "confirmed",
        },
        headers=await auth_headers(student),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["start_time"] is None
    assert body["location"] is None
    assert body["student_id"] == str(student.id)


async def test_a_student_request_needs_a_preferred_date(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    response = await client.post(
        APPOINTMENTS,
        json={"appointment_type": "consultation", "title": "Course advice"},
        headers=await auth_headers(student),
    )
    assert response.status_code == 422


async def test_a_student_cannot_request_for_someone_else(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    victim = await user_factory(UserRole.STUDENT)

    response = await client.post(
        APPOINTMENTS,
        json={
            "appointment_type": "consultation",
            "title": "Course advice",
            "preferred_date": "2027-03-01",
            "student_id": str(victim.id),
        },
        headers=await auth_headers(student),
    )
    assert response.json()["student_id"] == str(student.id)


async def test_staff_scheduling_notifies_the_student(
    client: AsyncClient,
    user_factory,
    auth_headers,
    session,
) -> None:
    from sqlalchemy import select

    from app.models import Notification

    counsellor = await user_factory(UserRole.COUNSELLOR)
    student = await user_factory(UserRole.STUDENT)

    response = await client.post(
        APPOINTMENTS,
        json={
            "appointment_type": "consultation",
            "title": "Intake call",
            "student_id": str(student.id),
            "start_time": "2027-03-01T09:00:00Z",
            "end_time": "2027-03-01T10:00:00Z",
        },
        headers=await auth_headers(counsellor),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "scheduled"

    notifications = (await session.scalars(select(Notification).where(Notification.user_id == student.id))).all()
    assert any(n.title == "Appointment scheduled" for n in notifications)


async def test_students_only_see_their_own_appointments(client: AsyncClient, user_factory, auth_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    mine = await user_factory(UserRole.STUDENT)
    theirs = await user_factory(UserRole.STUDENT)

    for student in (mine, theirs):
        await client.post(
            APPOINTMENTS,
            json={
                "appointment_type": "consultation",
                "title": "Call",
                "student_id": str(student.id),
                "start_time": "2027-03-01T09:00:00Z",
                "end_time": "2027-03-01T10:00:00Z",
            },
            headers=await auth_headers(counsellor),
        )

    response = await client.get(
        APPOINTMENTS,
        params={"student_id": str(theirs.id)},
        headers=await auth_headers(mine),
    )
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["student_id"] == str(mine.id)


# ── Payments ──────────────────────────────────────────────────────────────────


async def test_created_by_comes_from_the_token(client: AsyncClient, user_factory, auth_headers) -> None:
    finance = await user_factory(UserRole.FINANCE)
    student = await user_factory(UserRole.STUDENT)
    impostor = await user_factory(UserRole.ADMIN)

    response = await client.post(
        PAYMENTS,
        json={
            "student_id": str(student.id),
            "amount": 1500.0,
            "payment_method": "bank_transfer",
            "created_by": str(impostor.id),
        },
        headers=await auth_headers(finance),
    )
    assert response.status_code == 200, response.text
    assert response.json()["created_by"] == str(finance.id)


async def test_students_cannot_record_payments(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    response = await client.post(
        PAYMENTS,
        json={"student_id": str(student.id), "amount": 1.0, "payment_method": "cash"},
        headers=await auth_headers(student),
    )
    assert response.status_code == 403


async def test_students_only_see_their_own_payments(client: AsyncClient, user_factory, auth_headers) -> None:
    finance = await user_factory(UserRole.FINANCE)
    staff_headers = await auth_headers(finance)
    mine = await user_factory(UserRole.STUDENT)
    theirs = await user_factory(UserRole.STUDENT)

    for student in (mine, theirs):
        await client.post(
            PAYMENTS,
            json={"student_id": str(student.id), "amount": 10.0, "payment_method": "cash"},
            headers=staff_headers,
        )

    listed = await client.get(PAYMENTS, params={"student_id": str(theirs.id)}, headers=await auth_headers(mine))
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["student_id"] == str(mine.id)


# ── Tasks ─────────────────────────────────────────────────────────────────────


async def test_students_cannot_reach_tasks_at_all(client: AsyncClient, user_factory, auth_headers) -> None:
    """ED360 guards `POST /tasks` with `get_current_user`, so a student can
    assign work to staff."""
    student = await user_factory(UserRole.STUDENT)
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(student)

    assert (await client.get(TASKS, headers=headers)).status_code == 403
    created = await client.post(
        TASKS,
        json={"title": "Do my paperwork", "task_type": "follow_up", "assigned_to": str(counsellor.id)},
        headers=headers,
    )
    assert created.status_code == 403


async def test_assigned_by_comes_from_the_token(client: AsyncClient, user_factory, auth_headers) -> None:
    manager = await user_factory(UserRole.MANAGER)
    counsellor = await user_factory(UserRole.COUNSELLOR)
    other = await user_factory(UserRole.ADMIN)

    response = await client.post(
        TASKS,
        json={
            "title": "Call the applicant",
            "task_type": "follow_up",
            "assigned_to": str(counsellor.id),
            "assigned_by": str(other.id),
        },
        headers=await auth_headers(manager),
    )
    assert response.status_code == 200, response.text
    assert response.json()["assigned_by"] == str(manager.id)


async def test_only_admins_delete_tasks(client: AsyncClient, user_factory, auth_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(counsellor)
    task = await client.post(
        TASKS,
        json={"title": "Call", "task_type": "follow_up", "assigned_to": str(counsellor.id)},
        headers=headers,
    )
    task_id = task.json()["id"]

    assert (await client.delete(f"{TASKS}/{task_id}", headers=headers)).status_code == 403

    admin = await user_factory(UserRole.ADMIN)
    assert (await client.delete(f"{TASKS}/{task_id}", headers=await auth_headers(admin))).status_code == 200


# ── Notifications ─────────────────────────────────────────────────────────────


async def test_a_user_cannot_forge_a_notification_for_someone_else(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """ED360 guards `POST /notifications` with `get_current_user` while
    `user_id` is a body field — so one student can drop "Your visa was
    approved, pay here" into another's feed, wearing the system's voice."""
    attacker = await user_factory(UserRole.STUDENT)
    victim = await user_factory(UserRole.STUDENT)

    response = await client.post(
        NOTIFICATIONS,
        json={
            "user_id": str(victim.id),
            "type": "payment",
            "title": "Your visa was approved",
            "message": "Pay the balance at this link",
        },
        headers=await auth_headers(attacker),
    )
    assert response.status_code == 403


async def test_notifications_are_always_scoped_to_the_caller(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    recipient = await user_factory(UserRole.STUDENT)
    await client.post(
        NOTIFICATIONS,
        json={"user_id": str(recipient.id), "type": "system", "title": "Hello", "message": "Welcome"},
        headers=await auth_headers(admin),
    )

    other = await user_factory(UserRole.STUDENT)
    assert (await client.get(NOTIFICATIONS, headers=await auth_headers(other))).json()["total"] == 0
    assert (await client.get(NOTIFICATIONS, headers=await auth_headers(recipient))).json()["total"] == 1


async def test_a_user_marks_their_own_notification_read_and_deletes_it(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    recipient = await user_factory(UserRole.STUDENT)
    created = await client.post(
        NOTIFICATIONS,
        json={"user_id": str(recipient.id), "type": "system", "title": "Hello", "message": "Welcome"},
        headers=await auth_headers(admin),
    )
    notification_id = created.json()["id"]
    headers = await auth_headers(recipient)

    read = await client.post(f"{NOTIFICATIONS}/{notification_id}/read", headers=headers)
    assert read.status_code == 200
    assert read.json()["is_read"] is True

    # ED360 restricts delete to admins, leaving a user unable to dismiss
    # their own.
    assert (await client.delete(f"{NOTIFICATIONS}/{notification_id}", headers=headers)).status_code == 200


async def test_a_user_cannot_read_or_dismiss_a_stranger_notification(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    recipient = await user_factory(UserRole.STUDENT)
    created = await client.post(
        NOTIFICATIONS,
        json={"user_id": str(recipient.id), "type": "system", "title": "Hello", "message": "Welcome"},
        headers=await auth_headers(admin),
    )
    notification_id = created.json()["id"]

    intruder = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(intruder)
    assert (await client.get(f"{NOTIFICATIONS}/{notification_id}", headers=headers)).status_code == 403
    assert (await client.post(f"{NOTIFICATIONS}/{notification_id}/read", headers=headers)).status_code == 403
    assert (await client.delete(f"{NOTIFICATIONS}/{notification_id}", headers=headers)).status_code == 403
