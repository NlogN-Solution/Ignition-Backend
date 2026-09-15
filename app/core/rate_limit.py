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

#: The one public endpoint that writes. Looser than the credential limits —
#: a student correcting a typo and resubmitting is normal — but tight enough
#: that the lead table cannot be filled from a script.
ELIGIBILITY_RATE_LIMIT = "6/minute"

#: Pressing Apply Now. Looser again: the row it writes holds no personal data
#: (a programme id and the page they were on), and a student comparing four
#: courses legitimately mints four intents in a minute. Still bounded, because
#: it is an unauthenticated insert.
APPLY_INTENT_RATE_LIMIT = "30/minute"
