"""Failed-login counters keyed on *account and source address*.

The lockout used to be keyed on the account alone: five wrong passwords from
anywhere locked the owner out for fifteen minutes, so anyone who knew a
counsellor's e-mail could keep them out of the console indefinitely
(FAPI-SEC-011). Counting per `(email, ip)` means an attacker only ever locks
*themselves* out of that account; the account-wide lock in `AuthService`
remains, at a much higher threshold, as the backstop against guessing spread
over many addresses.

Redis in development and production, so the count holds across workers and
restarts; an in-process dict under `ENVIRONMENT=test`, the same trade-off
`core/rate_limit.py` makes. A fixed window (`INCR` + `EXPIRE`) is enough here —
the precision of a sliding window buys nothing for "five tries per quarter
hour".

**Fails open, deliberately.** If Redis is unreachable the counters read as zero
and a warning is logged. Refusing every login because a cache is down would
turn a Redis blip into a full outage, and the database-backed account lock
still bounds guessing in the meantime.
"""

from __future__ import annotations

import hashlib
import logging
import time

from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_PREFIX = "ignition:login-fail:"


def _key(email: str, ip: str | None) -> str:
    # Hashed so raw e-mail addresses are not sitting in Redis key names, where
    # `KEYS *` or a monitoring dashboard would list them.
    digest = hashlib.sha256(f"{email.strip().lower()}|{ip or '-'}".encode()).hexdigest()
    return f"{_PREFIX}{digest}"


class _MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[int, float]] = {}

    async def get(self, key: str) -> int:
        count, expires = self._data.get(key, (0, 0.0))
        if expires <= time.monotonic():
            self._data.pop(key, None)
            return 0
        return count

    async def incr(self, key: str, window_seconds: int) -> int:
        count = await self.get(key)
        expires = self._data.get(key, (0, time.monotonic() + window_seconds))[1]
        self._data[key] = (count + 1, expires)
        return count + 1

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _RedisStore:
    def __init__(self, url: str) -> None:
        from redis import asyncio as redis_asyncio

        self._client = redis_asyncio.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    async def get(self, key: str) -> int:
        value = await self._client.get(key)
        return int(value) if value is not None else 0

    async def incr(self, key: str, window_seconds: int) -> int:
        async with self._client.pipeline(transaction=True) as pipe:
            # One MULTI: create the key with its TTL only if absent, then count.
            # The window therefore starts at the first failure and later ones do
            # not extend it, and no key can ever exist without an expiry (which
            # a separate INCR-then-EXPIRE could leave behind if interrupted).
            pipe.set(key, 0, ex=window_seconds, nx=True)
            pipe.incr(key)
            _, count = await pipe.execute()
        return int(count)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)


class LoginThrottle:
    def __init__(self) -> None:
        self._store: _MemoryStore | _RedisStore = (
            _MemoryStore() if settings.ENVIRONMENT == "test" else _RedisStore(settings.redis_url)
        )

    @property
    def _window_seconds(self) -> int:
        return settings.ACCOUNT_LOCKOUT_MINUTES * 60

    async def is_blocked(self, email: str, ip: str | None) -> bool:
        try:
            return await self._store.get(_key(email, ip)) >= settings.MAX_FAILED_LOGIN_ATTEMPTS
        except Exception:
            logger.warning("Login throttle store unavailable; failing open", exc_info=True)
            return False

    async def record_failure(self, email: str, ip: str | None) -> None:
        try:
            await self._store.incr(_key(email, ip), self._window_seconds)
        except Exception:
            logger.warning("Login throttle store unavailable; failure not counted", exc_info=True)

    async def clear(self, email: str, ip: str | None) -> None:
        try:
            await self._store.delete(_key(email, ip))
        except Exception:
            logger.warning("Login throttle store unavailable; counter not cleared", exc_info=True)


login_throttle = LoginThrottle()

#: The `ip` slot used for "re-enter your password" checks made by someone who
#: is already signed in (change password, change e-mail). Those are a password
#: oracle for anyone holding a stolen access token, so they are throttled per
#: *account* regardless of source address.
REAUTH_SCOPE = "reauth"
