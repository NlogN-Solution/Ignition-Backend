"""Phase 6 — the student's journey checklist.

Writable, unlike progress and points: a student ticks their own items. What
they cannot do is decide what a tick is worth — that runs through the same
event bus as everything else in Phase 6, so these tests also confirm a
completed item pays through `task.complete` rather than directly.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models import ChecklistTemplateItem, PointsRule
from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"

#: Present in the database only to prove it is *not* copied to students.
TEMPLATE = [
    ("passport", "Secure your passport", None, None, 1),
    ("ielts", "Sit the IELTS exam", "passport", None, 2),
    ("sop", "Write your SOP", "ielts", None, 3),
]


@pytest_asyncio.fixture
async def catalog(session) -> None:
    for order, (key, title, depends_on, due_days, _order) in enumerate(TEMPLATE, start=1):
        session.add(
            ChecklistTemplateItem(
                key=key, title=title, stage=key, order=order, depends_on_key=depends_on, due_after_days=due_days
            )
        )
    session.add(PointsRule(action="task.complete", label="Checklist task completed", points=20, once_per_student=False))
    await session.commit()


@pytest_asyncio.fixture
async def student(client: AsyncClient, user_factory, auth_headers, catalog) -> dict:
    user = await user_factory(UserRole.STUDENT, email="checklist.student@example.com")
    return {"user": user, "headers": await auth_headers(user)}


async def test_the_checklist_starts_empty_even_with_a_template_on_file(client: AsyncClient, student) -> None:
    """Nothing is pre-filled: a template row in the database is not copied to
    the student, so there is no fixed ladder and no "0/3 done" to show."""
    response = await client.get(f"{STUDENT}/me/checklist", headers=student["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["completed"] == 0


async def test_completing_an_item_awards_points_once(client: AsyncClient, student) -> None:
    """Points key on the item's id, so toggling completion cannot farm the
    ledger."""
    headers = student["headers"]
    created = (await client.post(f"{STUDENT}/me/checklist", json={"title": "Book IELTS"}, headers=headers)).json()

    for completed in (True, False, True):
        response = await client.patch(
            f"{STUDENT}/me/checklist/{created['id']}", json={"completed": completed}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["is_complete"] is completed
        assert response.json()["is_locked"] is False

    points = (await client.get(f"{STUDENT}/me/points", headers=headers)).json()
    assert points["balance"] == 20
    assert len(points["history"]) == 1
    assert points["history"][0]["action"] == "task.complete"


async def test_a_student_can_add_and_complete_a_custom_item(client: AsyncClient, student) -> None:
    headers = student["headers"]
    created = await client.post(f"{STUDENT}/me/checklist", json={"title": "Book English tutoring"}, headers=headers)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["is_custom"] is True
    assert body["key"] is None

    # A custom item has no key and therefore no dependency to be locked behind.
    response = await client.patch(f"{STUDENT}/me/checklist/{body['id']}", json={"completed": True}, headers=headers)
    assert response.status_code == 200
    assert response.json()["is_complete"] is True


async def test_a_custom_item_can_be_deleted(client: AsyncClient, student) -> None:
    headers = student["headers"]
    created = await client.post(f"{STUDENT}/me/checklist", json={"title": "Something I made up"}, headers=headers)
    item_id = created.json()["id"]

    response = await client.delete(f"{STUDENT}/me/checklist/{item_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["success"] is True


async def test_a_student_cannot_see_or_touch_another_students_checklist(
    client: AsyncClient, student, user_factory, auth_headers
) -> None:
    other = await user_factory(UserRole.STUDENT, email="checklist.other@example.com")
    other_headers = await auth_headers(other)

    mine = (
        await client.post(f"{STUDENT}/me/checklist", json={"title": "Mine"}, headers=student["headers"])
    ).json()

    response = await client.patch(
        f"{STUDENT}/me/checklist/{mine['id']}", json={"completed": True}, headers=other_headers
    )
    assert response.status_code == 404
    assert (await client.get(f"{STUDENT}/me/checklist", headers=other_headers)).json()["items"] == []
