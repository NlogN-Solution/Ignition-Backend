from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.api.router import router
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_ctx
from app.core.middleware import RequestContextMiddleware
from app.core.rate_limit import limiter
from app.core.subscribers import register_subscribers

settings = get_settings()
configure_logging(json_output=settings.is_production)
logger = logging.getLogger(__name__)

# Wire event subscribers before any request can run (Phase 5b).
register_subscribers()

app = FastAPI(
    title="Ignition API",
    description="Single-tenant education platform — staff CRM and student portal.",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# slowapi resolves the limiter off app.state; the `@limiter.limit` decorators on
# the auth routes are inert without this.
app.state.limiter = limiter

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again shortly.", "request_id": request_id_ctx.get()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence: log the traceback, return an opaque body.

    Stack traces are operator information, never API responses.
    """
    logger.exception("Unhandled exception", extra={"path": request.url.path, "method": request.method})
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id_ctx.get()},
    )


app.include_router(router)
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
