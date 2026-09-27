from __future__ import annotations

import re
import time
import uuid

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logging import request_id_ctx

REQUEST_ID_HEADER = "X-Request-ID"

#: What an inbound X-Request-ID may look like. It is echoed into a response
#: header and into every log line for the request, so it is held to a short,
#: boring alphabet — a client-chosen 10 kB header would otherwise be copied
#: into each of them.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns every request an id, echoes it back, and logs the outcome.

    An inbound X-Request-ID is honoured so a trace survives the hop from nginx
    or from the frontend — but only if it looks like an id.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        inbound = request.headers.get(REQUEST_ID_HEADER)
        request_id = inbound if inbound and _REQUEST_ID_PATTERN.match(inbound) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative headers for a JSON API.

    Nothing here is load-bearing for the API's own authorisation; it is
    defence in depth for the day a response is rendered by a browser that was
    not expecting it (a file download, an error page, a misrouted link).
    """

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # Authenticated JSON — signed file links above all — must not be kept
        # by a shared cache or the browser's back/forward cache. Routes that
        # *want* caching (the public catalogue) set their own header first.
        headers.setdefault("Cache-Control", "no-store")
        if self.hsts:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class BodySizeLimitMiddleware:
    """Refuse oversized request bodies while they stream in (FAPI-SEC-012).

    Uploads used to be read whole into memory and only *then* measured, with no
    cap on the request as a whole — ten 200 MB parts on one message were all
    held before any check ran. This sits in front of everything, as raw ASGI so
    it sees the body chunk by chunk:

    * a declared `Content-Length` over the limit is refused before a byte of
      the body is read;
    * a chunked (or lying) body is counted as it arrives and cut off the moment
      it crosses the limit.

    Either way the answer is 413. Per-file limits still apply downstream in
    `core/uploads.store_upload`; this is the ceiling for a whole request.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.max_body_bytes
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await self._reject(scope, receive, send, status=400, detail="Invalid Content-Length header")
                    return
                if declared > limit:
                    await self._reject(scope, receive, send, status=413, detail=self._too_large())
                    return
                break

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge(self._too_large())
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            # Normally unreachable: `_BodyTooLarge` is an HTTPException, so the
            # app's own exception middleware turns it into the 413. This is the
            # backstop for a path that lets it escape.
            if not response_started:
                await self._reject(scope, receive, send, status=413, detail=self._too_large())

    def _too_large(self) -> str:
        return f"Request body exceeds the {self.max_body_bytes // (1024 * 1024)} MB limit"

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, *, status: int, detail: str) -> None:
        response = JSONResponse(status_code=status, content={"detail": detail}, headers={"Connection": "close"})
        await response(scope, receive, send)


class _BodyTooLarge(HTTPException):
    """Raised inside the receive channel to unwind the app mid-body.

    FastAPI's own HTTPException on purpose: its body parsing re-raises exactly
    that class as-is but turns any other exception — Starlette's base
    HTTPException included — into a generic 400.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=413, detail=detail)
