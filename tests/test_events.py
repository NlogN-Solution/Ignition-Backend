"""Phase 5b — the event bus.

Unit tests for the bus itself, plus one integration test proving a service
emits and a subscriber acts, so the wiring is covered end to end rather than
only in isolation.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.events import (
    ApplicationStatusChanged,
    DocumentApproved,
    Event,
    EventBus,
)

pytestmark = pytest.mark.asyncio


async def test_a_subscriber_receives_its_event() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: ApplicationStatusChanged, session: object) -> None:
        received.append(event)

    bus.subscribe(ApplicationStatusChanged, handler)
    event = ApplicationStatusChanged(
        application_id=uuid4(), student_id=uuid4(), old_status="draft", new_status="submitted"
    )
    await bus.publish(event, None)

    assert received == [event]


async def test_handlers_only_receive_their_own_event_type() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def on_status(event: ApplicationStatusChanged, session: object) -> None:
        seen.append("status")

    async def on_document(event: DocumentApproved, session: object) -> None:
        seen.append("document")

    bus.subscribe(ApplicationStatusChanged, on_status)
    bus.subscribe(DocumentApproved, on_document)

    await bus.publish(
        ApplicationStatusChanged(application_id=uuid4(), student_id=uuid4(), old_status=None, new_status="submitted"),
        None,
    )
    assert seen == ["status"]


async def test_one_failing_handler_does_not_stop_the_others() -> None:
    """Subscribers are independent consequences of the same fact, not a
    pipeline — a broken notification must not suppress the audit log."""
    bus = EventBus()
    ran: list[str] = []

    async def broken(event: Event, session: object) -> None:
        raise RuntimeError("subscriber exploded")

    async def healthy(event: Event, session: object) -> None:
        ran.append("healthy")

    bus.subscribe(DocumentApproved, broken)
    bus.subscribe(DocumentApproved, healthy)

    await bus.publish(
        DocumentApproved(document_id=uuid4(), student_id=uuid4(), verified_by=uuid4(), title="passport.pdf"),
        None,
    )
    assert ran == ["healthy"]


async def test_publishing_with_no_subscribers_is_a_no_op() -> None:
    await EventBus().publish(
        DocumentApproved(document_id=uuid4(), student_id=uuid4(), verified_by=uuid4(), title="x.pdf"),
        None,
    )


async def test_events_are_immutable() -> None:
    """A subscriber must not be able to change what later subscribers see."""
    event = DocumentApproved(document_id=uuid4(), student_id=uuid4(), verified_by=uuid4(), title="x.pdf")
    with pytest.raises((AttributeError, TypeError)):
        event.title = "tampered"  # type: ignore[misc]


async def test_events_carry_an_occurred_at() -> None:
    event = DocumentApproved(document_id=uuid4(), student_id=uuid4(), verified_by=uuid4(), title="x.pdf")
    assert event.occurred_at is not None
    assert event.name == "document.approved"


# ── End to end through the real services ──────────────────────────────────────


async def test_approving_a_document_notifies_the_student_through_the_bus(
    client,
    user_factory,
    auth_headers,
    session,
) -> None:
    """The notification is no longer raised inline in the route: the service
    emits, and the subscriber writes it. This proves the wiring holds through
    an actual HTTP call."""
    from sqlalchemy import select

    from app.models import Notification

    student = await user_factory(__import__("app.models.enums", fromlist=["UserRole"]).UserRole.STUDENT)
    student_headers = await auth_headers(student)

    uploaded = await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=student_headers,
    )
    assert uploaded.status_code == 200, uploaded.text

    counsellor = await user_factory(__import__("app.models.enums", fromlist=["UserRole"]).UserRole.COUNSELLOR)
    approved = await client.post(
        f"/api/v1/documents/{uploaded.json()['id']}/verify",
        json={},
        headers=await auth_headers(counsellor),
    )
    assert approved.status_code == 200, approved.text

    notifications = (await session.scalars(select(Notification).where(Notification.user_id == student.id))).all()
    assert any(n.title == "Document approved" for n in notifications), (
        "the DocumentApproved subscriber should have written a notification"
    )
