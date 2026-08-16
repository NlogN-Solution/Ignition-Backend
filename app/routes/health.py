from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", summary="Liveness", description="The process is up and serving.")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness",
    description="Dependencies are reachable. Used by the load balancer to decide whether to route traffic.",
)
async def ready(session: AsyncSession = Depends(get_db_session)) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        # The response stays deliberately vague — it is public. The full reason
        # goes to the log, where the operator debugging a degraded probe can
        # actually read it.
        logger.exception("Readiness check failed: database unreachable")
        checks["database"] = f"error: {type(exc).__name__}"

    ready = all(value == "ok" for value in checks.values())
    return {"status": "ready" if ready else "degraded", "checks": checks}
