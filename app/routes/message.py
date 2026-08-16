from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..models import Message, User
from ..models.enums import NotificationType
from ..schemas.message import MessageCreate, MessageList, MessageRead, MessageSenderSummary, MessageThreadSummary
from ..services.notification_service import NotificationService, get_notification_service

router = APIRouter(prefix="/messages", tags=["Messages"])

#: Every role that fields student conversations in the CRM's Communication
#: page — broader than the review-only roles, since replying to a student
#: isn't a document-verification action.
STAFF_MESSAGE_ROLES = ("admin", "super_admin", "counsellor", "admissions", "manager", "frontdesk")


def _with_sender_summary(message: Message) -> MessageRead:
    read = MessageRead.model_validate(message)
    if message.sender:
        read.sender = MessageSenderSummary(
            id=message.sender.id,
            full_name=f"{message.sender.first_name} {message.sender.last_name}".strip(),
        )
    return read


@router.get("/threads", response_model=list[MessageThreadSummary], summary="List student conversations")
async def list_message_threads(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_role(*STAFF_MESSAGE_ROLES)),
) -> list[MessageThreadSummary]:
    result = await session.execute(
        select(Message, User).join(User, User.id == Message.student_id).order_by(Message.created_at.desc())
    )
    threads: dict[UUID, MessageThreadSummary] = {}
    for message, student in result.all():
        if message.is_from_student and not message.is_read:
            if message.student_id in threads:
                threads[message.student_id].unread_count += 1
            else:
                threads[message.student_id] = MessageThreadSummary(
                    student_id=message.student_id,
                    student_name=f"{student.first_name} {student.last_name}".strip(),
                    last_message=message.body,
                    last_message_from_student=message.is_from_student,
                    last_message_at=message.created_at,
                    unread_count=1,
                )
        elif message.student_id not in threads:
            threads[message.student_id] = MessageThreadSummary(
                student_id=message.student_id,
                student_name=f"{student.first_name} {student.last_name}".strip(),
                last_message=message.body,
                last_message_from_student=message.is_from_student,
                last_message_at=message.created_at,
                unread_count=0,
            )
    return sorted(threads.values(), key=lambda t: t.last_message_at, reverse=True)


@router.get("/{student_id}", response_model=MessageList, summary="One student's conversation")
async def get_student_thread(
    student_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_role(*STAFF_MESSAGE_ROLES)),
) -> MessageList:
    result = await session.execute(
        select(Message)
        .options(selectinload(Message.sender))
        .where(Message.student_id == student_id)
        .order_by(Message.created_at.asc())
        .limit(200)
    )
    items = list(result.scalars().all())
    unread_count = sum(1 for m in items if m.is_from_student and not m.is_read)
    return MessageList(items=[_with_sender_summary(m) for m in items], total=len(items), unread_count=unread_count)


@router.post("/{student_id}", response_model=MessageRead, summary="Reply to a student")
async def send_staff_message(
    student_id: UUID,
    payload: MessageCreate,
    session: AsyncSession = Depends(get_db_session),
    notification_service: NotificationService = Depends(get_notification_service),
    staff: User = Depends(require_role(*STAFF_MESSAGE_ROLES)),
) -> MessageRead:
    message = Message(
        student_id=student_id,
        sender_id=staff.id,
        is_from_student=False,
        body=payload.body,
        is_read=False,
    )
    session.add(message)
    await session.commit()
    reloaded = await session.scalar(
        select(Message).options(selectinload(Message.sender)).where(Message.id == message.id)
    )
    assert reloaded is not None
    await notification_service.notify_many(
        [student_id],
        notification_type=NotificationType.MESSAGE,
        title="New message",
        message="You have a new message from support.",
    )
    return _with_sender_summary(reloaded)


@router.post("/{student_id}/read-all", summary="Mark a student's messages read")
async def mark_thread_read(
    student_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_role(*STAFF_MESSAGE_ROLES)),
) -> dict[str, int]:
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(Message)
            .where(Message.student_id == student_id, Message.is_from_student.is_(True), Message.is_read.is_(False))
            .values(is_read=True)
        ),
    )
    await session.commit()
    return {"updated": result.rowcount}
