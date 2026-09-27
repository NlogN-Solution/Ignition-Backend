from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_owner
from ..api.deps import get_db_session
from ..core.client_ip import client_ip
from ..core.config import get_settings

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


@router.get(
    "/client-ip",
    summary="How this request's client address was resolved (owner only)",
    description=(
        "Deployment check for TRUSTED_PROXY_HOPS. Call it from a browser as the super-admin: "
        "`resolved` must be your own public IP. If it is a proxy's address, the hop count is too "
        "low; if it echoes a value you put in X-Forwarded-For yourself, it is too high."
    ),
)
async def client_ip_diagnostic(request: Request, _: object = Depends(require_owner)) -> dict[str, object]:
    settings = get_settings()
    return {
        "resolved": client_ip(request),
        "socket_peer": request.client.host if request.client else None,
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "trusted_proxy_hops": settings.TRUSTED_PROXY_HOPS,
        "client_ip_header": settings.CLIENT_IP_HEADER,
    }
