from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.api.router import router
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_ctx
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.rate_limit import limiter
from app.core.subscribers import register_subscribers

settings = get_settings()
configure_logging(json_output=settings.is_production)
logger = logging.getLogger(__name__)

# Wire event subscribers before any request can run (Phase 5b).
register_subscribers()


def _warn_on_hosted_database_in_development() -> None:
    """FAPI-SEC-017: a development server pointed at a hosted database.

    Development mode turns on /docs, a permissive LAN CORS pattern and a
    per-process JWT secret. None of that is safe in front of real data, and
    the combination is easy to reach by copying a production DATABASE_URL into
    a local `.env`. This cannot tell a Neon *dev branch* from production, so it
    warns rather than refuses — loudly, once, at startup.
    """
    if settings.ENVIRONMENT != "development" or not settings.DATABASE_URL:
        return
    try:
        host = make_url(settings.DATABASE_URL).host or ""
    except Exception:  # pragma: no cover - malformed URL fails later, loudly
        return
    if host not in {"localhost", "127.0.0.1", "::1", "db", "postgres"}:
        logger.warning(
            "ENVIRONMENT=development is using a non-local database host. Make sure this is NOT the "
            "production database: development mode exposes /docs and a permissive CORS policy."
        )


_warn_on_hosted_database_in_development()

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

# Order matters: Starlette runs the *last* added middleware first.
#
# The body limit is innermost, directly around the app: the two
# BaseHTTPMiddleware layers read the body through an anyio task group, which
# would wrap its mid-stream 413 in an ExceptionGroup that FastAPI then reports
# as a generic 400. In here it still runs before any route code, and its 413
# picks up the request id and security headers on the way out. CORS stays
# outermost so every error, 413 included, carries the headers a browser needs
# to show the real status instead of a generic CORS failure.
app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.MAX_REQUEST_BODY_MB * 1024 * 1024)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.cors_origin_regex,
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


#: Postgres SQLSTATEs worth telling apart. Everything else is a generic 409.
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """A constraint the database enforced that the handler did not check first.

    Handlers are expected to validate references and uniqueness themselves and
    answer with a precise 404/409. This is the backstop, so a race or a missed
    check is a 409 with a neutral message — not a 500 whose timing and request
    id tell the caller they hit a unique index (FAPI-SEC-004, FAPI-SEC-009).
    """
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == _UNIQUE_VIOLATION:
        detail = "A record with these details already exists."
    elif sqlstate == _FOREIGN_KEY_VIOLATION:
        detail = "A referenced record does not exist."
    else:
        detail = "The request conflicts with existing data."
    logger.info("Integrity error mapped to 409", extra={"path": request.url.path, "sqlstate": sqlstate})
    return JSONResponse(status_code=409, content={"detail": detail, "request_id": request_id_ctx.get()})


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
