"""In-process, synchronous, typed event bus.

Phase 5b. No Kafka, no broker: handlers run in the same process and the same
request, immediately after the emitting service commits.

Why this exists: ED360 raises notifications inline in route handlers, so the
rule "a student is told when their document is approved" lives in
`routes/document.py` rather than anywhere a reader would look for it, and any
new caller of `verify_document` silently skips it. Emitting an event from the
service moves that rule to a subscriber and makes it apply to every caller.

Dispatch is deliberately *after commit*, and handlers are handed the emitting
session. By then that session's transaction has closed, so a handler writing
through it opens a fresh one — the causing change is already durable and cannot
be rolled back by a failing subscriber.

Passing the session rather than opening a new one per handler matters twice: a
handler can read rows the request just wrote without racing its own connection,
and the whole mechanism stays exercisable by the transaction-rollback test
harness. A subscriber that opened its own connection could not see uncommitted
test data and failed with a foreign-key violation.

Handler exceptions are logged and swallowed: a notification failing to write is
not a reason to un-approve a document. A handler that must not be lost should
enqueue durable work rather than rely on the emitting request surviving.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, TypeVar
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base for every domain event.

    Frozen: a subscriber must not be able to mutate what later subscribers see.
    """

    #: Stable name used in logs and by `subscribe`.
    name: ClassVar[str] = "event"

    #: Set at construction. `default_factory` rather than a `__post_init__`
    #: fixup so the field is genuinely a datetime and mypy can see it.
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True, kw_only=True)
class StudentCreated(Event):
    name: ClassVar[str] = "student.created"
    student_id: UUID
    email: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationCreated(Event):
    name: ClassVar[str] = "application.created"
    application_id: UUID
    student_id: UUID
    created_by: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationStatusChanged(Event):
    name: ClassVar[str] = "application.status_changed"
    application_id: UUID
    student_id: UUID
    old_status: str | None
    new_status: str
    changed_by: UUID | None = None
    remarks: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentUploaded(Event):
    name: ClassVar[str] = "document.uploaded"
    document_id: UUID
    student_id: UUID
    uploaded_by: UUID | None
    title: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentApproved(Event):
    name: ClassVar[str] = "document.approved"
    document_id: UUID
    student_id: UUID
    verified_by: UUID
    title: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentRejected(Event):
    name: ClassVar[str] = "document.rejected"
    document_id: UUID
    student_id: UUID
    verified_by: UUID
    title: str
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AppointmentScheduled(Event):
    name: ClassVar[str] = "appointment.scheduled"
    appointment_id: UUID
    student_id: UUID | None
    title: str
    #: Who to tell depends on this: a student's REQUESTED slot notifies the
    #: staff responsible for them, a staff-booked one notifies the student.
    status: str
    requested_by_student: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentCompleted(Event):
    name: ClassVar[str] = "payment.completed"
    payment_id: UUID
    student_id: UUID
    amount: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ChecklistItemCompleted(Event):
    """A student ticked off one of their own journey items.

    Emitted rather than awarding points inline, so the checklist service never
    needs to know what a completed item is worth — and so the one rule that
    points move only through subscribers survives a module the student can write
    to.
    """

    name: ClassVar[str] = "checklist.item_completed"
    item_id: UUID
    student_id: UUID
    key: str | None
    title: str


@dataclass(frozen=True, slots=True, kw_only=True)
class InterviewCompleted(Event):
    """A student finished a mock interview session — win or lose.

    The "interview" milestone is about reaching this stage of the journey, not
    about scoring well on it: the same status transitions that complete "offer"
    or "visa" fire regardless of quality, so this does too.
    """

    name: ClassVar[str] = "interview.completed"
    session_id: UUID
    student_id: UUID
    type_key: str
    score: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskAssigned(Event):
    name: ClassVar[str] = "task.assigned"
    task_id: UUID
    assigned_to: UUID
    assigned_by: UUID | None
    title: str


EventT = TypeVar("EventT", bound=Event)
#: Handlers take the event and the emitting session.
Handler = Callable[[Any, Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[EventT], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: Event, session: Any) -> None:
        """Run every handler for this event type.

        Handlers run in registration order, and one failing does not stop the
        rest: subscribers are independent consequences of the same fact, not a
        pipeline.
        """
        for handler in self._handlers[type(event)]:
            try:
                await handler(event, session)
            except Exception:
                logger.exception(
                    "Event handler failed",
                    extra={"event": event.name, "handler": getattr(handler, "__qualname__", repr(handler))},
                )

    def clear(self) -> None:
        """Drop all subscriptions — used by tests to isolate registrations."""
        self._handlers.clear()

    def handler_count(self, event_type: type[Event]) -> int:
        return len(self._handlers[event_type])


#: Process-wide bus. Subscribers register at import time in `app.core.subscribers`.
event_bus = EventBus()
