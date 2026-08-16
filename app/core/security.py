from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from .config import get_settings

settings = get_settings()

# ED360 uses `passlib.CryptContext` here. That does not work with the pinned
# bcrypt 5.0.0: passlib 1.7.4 (last released 2020) reads
# `bcrypt.__about__.__version__`, which bcrypt removed in 4.1, then falls back
# to a legacy probe that hands bcrypt a 73-byte secret — which bcrypt 5 rejects
# outright. The result is that *every* call raises, not just long passwords.
# passlib is unmaintained, so this calls bcrypt directly rather than pinning the
# ecosystem backwards.

# 12 rounds is the production cost. The test suite creates a user per fixture
# and pays it on every one, which took the run past two minutes — 4 rounds keeps
# the same code path (and the same stored format) at a fraction of the cost.
BCRYPT_ROUNDS = 4 if settings.ENVIRONMENT == "test" else 12


def create_access_token(
    subject: str,
    role: str | None = None,
) -> str:
    """Short-lived bearer token.

    ED360's version also carried `organization_id`; there is one tenant here, so
    the claim has no meaning (strip rule R7).
    """
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access"}
    if role:
        payload["role"] = role
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    """Refresh token, its lookup hash, and its expiry.

    Returns three values where ED360 returned one, because a refresh token is
    only honoured here if a matching live `UserSession` row exists. The caller
    persists `token_hash`; `revoke`/`rotate` then have something to act on.

    The `jti` is what makes rotation work: without it two tokens minted for the
    same user in the same second are byte-identical, so revoking one would
    revoke the other.
    """
    expires_at = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": subject,
        "exp": expires_at,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, hash_refresh_token(token), expires_at


def hash_refresh_token(token: str) -> str:
    """Lookup hash for `user_sessions.refresh_token_hash`.

    SHA-256, not bcrypt: this is looked up by equality on every refresh, and the
    input is 200+ bits of signed, unguessable token rather than a human-chosen
    password — so bcrypt's work factor would buy nothing and cost an indexed
    lookup per request.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises `jwt.PyJWTError` if invalid or expired."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def _prepare(password: str) -> bytes:
    """Condense a password to a fixed 44 bytes for bcrypt.

    bcrypt takes at most 72 bytes and raises above that, so a long passphrase
    would otherwise 500 on signup. Pre-hashing removes the ceiling without the
    silent truncation that older bcrypt versions did — and truncation is not
    merely untidy: it makes two passwords sharing a 72-byte prefix equivalent.

    SHA-256 digest → base64 is always 44 bytes, safely under the limit. Base64
    rather than raw digest because a raw digest can contain a NUL byte, at which
    point C bcrypt stops reading and the effective password is whatever preceded
    it.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_prepare(plain_password), hashed_password.encode("utf-8"))
    except ValueError:
        # Malformed/truncated hash in the column — treat as a failed match
        # rather than a 500.
        return False


def generate_temporary_password(length: int = 16) -> str:
    """URL-safe random password for staff accounts created by an admin.

    Paired with `must_change_password=True` so it is single-use in practice.
    """
    return secrets.token_urlsafe(length)
