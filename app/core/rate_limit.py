from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import get_settings

settings = get_settings()

# Redis-backed so the limit holds across workers — an in-memory counter would
# let a 4-worker deployment serve 4x the intended rate. Tests run with
# ENVIRONMENT=test and fall back to in-memory, which keeps the suite from
# needing Redis.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=None if settings.ENVIRONMENT == "test" else settings.redis_url,
    default_limits=[],
    enabled=settings.ENVIRONMENT != "test",
)

#: Credential endpoints. Tight, because these are the ones worth guessing at.
LOGIN_RATE_LIMIT = "10/minute"
REGISTER_RATE_LIMIT = "5/minute"
PASSWORD_RATE_LIMIT = "5/minute"
REFRESH_RATE_LIMIT = "30/minute"
