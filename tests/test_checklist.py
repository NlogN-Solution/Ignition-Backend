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


async def test_first_read_materialises_the_template(client: AsyncClient, student) -> None:
    """A student who has never fetched the checklist gets the whole template on
    first read, in template order."""
    response = await client.get(f"{STUDENT}/me/checklist", headers=student["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["key"] for i in body["items"]] == ["passport", "ielts", "sop"]
    assert body["total"] == 3
    assert body["completed"] == 0


async def test_materialise_is_idempotent(client: AsyncClient, student) -> None:
    """Fetching twice must not duplicate the checklist."""
    await client.get(f"{STUDENT}/me/checklist", headers=student["headers"])
    response = await client.get(f"{STUDENT}/me/checklist", headers=student["headers"])
    assert response.json()["total"] == 3


async def test_items_after_the_first_start_locked(client: AsyncClient, student) -> None:
    body = (await client.get(f"{STUDENT}/me/checklist", headers=student["headers"])).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["passport"]["is_locked"] is False
    assert by_key["ielts"]["is_locked"] is True
    assert by_key["sop"]["is_locked"] is True


async def test_completing_a_locked_item_is_refused(client: AsyncClient, student) -> None:
    body = (await client.get(f"{STUDENT}/me/checklist", headers=student["headers"])).json()
    ielts_id = next(i["id"] for i in body["items"] if i["key"] == "ielts")

    response = await client.patch(
        f"{STUDENT}/me/checklist/{ielts_id}", json={"completed": True}, headers=student["headers"]
    )
    assert response.status_code == 400
    assert "passport" in response.json()["detail"].lower() or "first" in response.json()["detail"].lower()


async def test_completing_in_order_unlocks_the_next_item_and_awards_points(client: AsyncClient, student) -> None:
    headers = student["headers"]
    body = (await client.get(f"{STUDENT}/me/checklist", headers=headers)).json()
    passport_id = next(i["id"] for i in body["items"] if i["key"] == "passport")

    response = await client.patch(f"{STUDENT}/me/checklist/{passport_id}", json={"completed": True}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["is_complete"] is True

    after = (await client.get(f"{STUDENT}/me/checklist", headers=headers)).json()
    by_key = {i["key"]: i for i in after["items"]}
    assert by_key["passport"]["is_complete"] is True
    assert by_key["ielts"]["is_locked"] is False
    assert after["completed"] == 1

    points = (await client.get(f"{STUDENT}/me/points", headers=headers)).json()
    assert points["balance"] == 20
    assert points["history"][0]["action"] == "task.complete"


async def test_untick_then_retick_does_not_pay_twice(client: AsyncClient, student) -> None:
    """Points key on the item's id, so toggling completion cannot farm the
    ledger."""
    headers = student["headers"]
    body = (await client.get(f"{STUDENT}/me/checklist", headers=headers)).json()
    passport_id = next(i["id"] for i in body["items"] if i["key"] == "passport")

    await client.patch(f"{STUDENT}/me/checklist/{passport_id}", json={"completed": True}, headers=headers)
    await client.patch(f"{STUDENT}/me/checklist/{passport_id}", json={"completed": False}, headers=headers)
    await client.patch(f"{STUDENT}/me/checklist/{passport_id}", json={"completed": True}, headers=headers)

    points = (await client.get(f"{STUDENT}/me/points", headers=headers)).json()
    assert points["balance"] == 20
    assert len(points["history"]) == 1


async def test_untick_is_always_allowed_even_though_it_is_the_only_locked_prerequisite(
    client: AsyncClient, student
) -> None:
    headers = student["headers"]
    body = (await client.get(f"{STUDENT}/me/checklist", headers=headers)).json()
    passport_id = next(i["id"] for i in body["items"] if i["key"] == "passport")
    await client.patch(f"{STUDENT}/me/checklist/{passport_id}", json={"completed": True}, headers=headers)

    response = await client.patch(f"{STUDENT}/me/checklist/{passport_id}", json={"completed": False}, headers=headers)
    assert response.status_code == 200
    assert response.json()["is_complete"] is False


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


async def test_a_seeded_item_cannot_be_renamed_or_deleted(client: AsyncClient, student) -> None:
    headers = student["headers"]
    body = (await client.get(f"{STUDENT}/me/checklist", headers=headers)).json()
    passport_id = next(i["id"] for i in body["items"] if i["key"] == "passport")

    renamed = await client.patch(f"{STUDENT}/me/checklist/{passport_id}", json={"title": "Renamed"}, headers=headers)
    assert renamed.status_code == 400

    deleted = await client.delete(f"{STUDENT}/me/checklist/{passport_id}", headers=headers)
    assert deleted.status_code == 400


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

    mine = (await client.get(f"{STUDENT}/me/checklist", headers=student["headers"])).json()
    passport_id = next(i["id"] for i in mine["items"] if i["key"] == "passport")

    response = await client.patch(
        f"{STUDENT}/me/checklist/{passport_id}", json={"completed": True}, headers=other_headers
    )
    assert response.status_code == 404
