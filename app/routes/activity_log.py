from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role
from ..models import User
from ..schemas.activity_log import ActivityLogList, ActivityLogRead
from ..services.activity_log_service import ActivityLogService, get_activity_log_service

router = APIRouter(prefix="/activity-logs", tags=["Activity Logs"])


@router.get("", response_model=ActivityLogList, summary="List activity logs")
async def list_activity_logs(
    page: int = 1,
    limit: int = 20,
    user_id: UUID | None = None,
    activity_type: str | None = None,
    entity_type: str | None = None,
    service: ActivityLogService = Depends(get_activity_log_service),
    user: User = Depends(require_role("admin", "super_admin")),
) -> ActivityLogList:
    logs, total = await service.list_logs(
        page=page,
        limit=limit,
        user_id=user_id,
        activity_type=activity_type,
        entity_type=entity_type,
    )
    items = [
        ActivityLogRead(
            id=log.id,
            user_id=log.user_id,
            user_name=f"{log.user.first_name} {log.user.last_name}" if log.user else None,
            user_email=log.user.email if log.user else None,
            activity_type=log.activity_type,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            description=log.description,
            ip_address=str(log.ip_address) if log.ip_address is not None else None,
            user_agent=log.user_agent,
            created_at=log.created_at,
        )
        for log in logs
    ]
    return ActivityLogList(items=items, total=total, page=page, limit=limit)
