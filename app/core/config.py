from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parents[2]

# ED360's placeholder. If it ever appears here it means someone copied a .env
# across instead of generating a secret — refuse to boot on it in production.
_INSECURE_SECRETS = {"change-me-super-secret", "changeme", "secret"}


class Settings(BaseSettings):
    """Typed, validated settings.

    Every credential-bearing field is `repr=False`. The settings object turns up
    in tracebacks, pytest assertion output and debug logs, and its default repr
    printed DATABASE_URL — password included — to all of them.

    Deliberately different from ED360's hand-rolled `os.getenv` class: an
    invalid or missing value fails at import time with a readable error rather
    than surfacing as a confusing runtime failure later.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "test", "production"] = "development"

    #: No payment gateway is integrated yet. While this is true, the portal's
    #: access-fee checkout completes without money moving, and every surface
    #: that touches it says so in as many words. False makes the checkout
    #: refuse — it must never be possible to take a payment silently.
    #:
    #: **Defaults to False and cannot be True in production** (FAPI-SEC-001).
    #: It used to default to True, and nothing turned it off on Render, so any
    #: self-registered student could press "pay", move no money, and unlock
    #: the offer and CAS letters the fee exists to charge for. A demo switch
    #: has to fail closed: set `SIMULATED_PAYMENTS=true` explicitly in a
    #: development `.env` to demo the checkout.
    SIMULATED_PAYMENTS: bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    # Local dev composes a URL from the individual fields below. A hosted
    # Postgres (Neon, Render Postgres, ...) instead hands out one connection
    # string — set DATABASE_URL and it overrides DB_HOST/PORT/NAME/USER/PASSWORD
    # entirely rather than needing them parsed apart.
    DATABASE_URL: str | None = Field(default=None, repr=False)
    DB_HOST: str = "localhost"
    DB_PORT: int = 5433
    DB_NAME: str = "ignition"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = Field(default="postgres", repr=False)
    DB_POOL_SIZE: int = 5
    DB_ECHO: bool = False

    # ── Auth ──────────────────────────────────────────────────────────────────
    # No default. In development one is generated per process (see the validator
    # below) so `docker compose up` works out of the box; in production a missing
    # or weak secret is a hard failure.
    JWT_SECRET_KEY: str = Field(default="", repr=False)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Brute-force lockout. ED360's `users` table models both columns and never
    # writes either; `AuthService.authenticate` enforces them here.
    #
    # Two tiers (FAPI-SEC-011). `MAX_FAILED_LOGIN_ATTEMPTS` is counted per
    # *account and source IP*: five wrong passwords from one address stop that
    # address trying that account for `ACCOUNT_LOCKOUT_MINUTES`, without
    # locking the owner out from their own machine. `ACCOUNT_LOCKOUT_THRESHOLD`
    # is the account-wide backstop against a distributed guesser — it needs
    # failures from several addresses, so one attacker can no longer lock any
    # counsellor out by knowing their e-mail. Both answer exactly like a wrong
    # password, so neither reveals that the address is registered.
    MAX_FAILED_LOGIN_ATTEMPTS: int = Field(default=5, ge=1)
    ACCOUNT_LOCKOUT_THRESHOLD: int = Field(default=20, ge=1)
    ACCOUNT_LOCKOUT_MINUTES: int = Field(default=15, ge=1)

    #: A refresh token presented again within this many seconds of being
    #: rotated is treated as two tabs racing, not as theft: the loser gets a
    #: 401 and nothing else happens. Outside the window, reuse of a rotated
    #: token revokes every session the user has (FAPI-SEC-016).
    REFRESH_REUSE_GRACE_SECONDS: int = Field(default=10, ge=0)

    # ── Client address (FAPI-SEC-010) ─────────────────────────────────────────
    # See core/client_ip.py. How many reverse proxies sit in front of the app
    # and append to X-Forwarded-For. 0 = use the socket peer (local/dev).
    # Render: 1. Setting it higher than reality makes the client IP spoofable.
    TRUSTED_PROXY_HOPS: int = Field(default=0, ge=0, le=5)
    #: A header the edge *overwrites* with the connecting address (e.g.
    #: `CF-Connecting-IP`). Only when the app is unreachable except through it.
    CLIENT_IP_HEADER: str | None = None

    # ── Redis ─────────────────────────────────────────────────────────────────
    # Same override pattern as DATABASE_URL — a managed Redis (Render Key Value,
    # etc.) hands out one connection string rather than host/port pieces.
    REDIS_URL: str | None = Field(default=None, repr=False)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6380
    REDIS_DB: int = 0

    # ── Cloudinary ────────────────────────────────────────────────────────────
    # Media (avatars, documents, leave attachments) is stored on Cloudinary, not
    # on local/ephemeral disk — see core/uploads.py. Required in production;
    # ENVIRONMENT=test never calls Cloudinary (see uploads.py), so these stay
    # blank in the test suite.
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = Field(default="", repr=False)
    CLOUDINARY_API_SECRET: str = Field(default="", repr=False)

    # ── HTTP ──────────────────────────────────────────────────────────────────
    # 5174 = admin dashboard (Vite), 3001 = student portal (CRA),
    # 3100 = Ignition-Landing (Next). ED360's own stack occupies 5173/8000 on
    # this machine, hence the offsets. 3000 stays allowed because a plain
    # `next dev` lands the landing site there.
    #
    # The landing origin is here because the public catalogue routes are read
    # by the browser as well as by Next's server-side fetches, and a
    # server-side fetch is not subject to CORS while a client-side one is.
    CORS_ORIGINS: list[str] = Field(
        default=[
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:3100",
            "http://127.0.0.1:3100",
        ]
    )

    #: Extra origins matched by pattern. Left unset, development accepts any
    #: localhost / 127.0.0.1 / private-LAN (192.168.*, 10.*, 172.16–31.*)
    #: origin on any port — CRA and Vite both print an "On Your Network"
    #: address, and opening the app through it was a CORS failure on every
    #: request because no fixed list can name a DHCP-assigned IP. Production
    #: never gets the default: there, only `CORS_ORIGINS` (plus this, if set).
    CORS_ORIGIN_REGEX: str | None = None

    # ── Landing site ──────────────────────────────────────────────────────────
    # Where Ignition-Landing is served from, and the secret it shares with this
    # API. Both sides need it: the admin publishes a page and the Next cache
    # has to be told, and an editor wants to see a draft before anyone else
    # can. Left empty — the default — publishing simply does not call out, so
    # a developer with no landing running is not spammed with failures.
    LANDING_BASE_URL: str = "http://localhost:3000"
    LANDING_REVALIDATE_SECRET: str = Field(default="", repr=False)
    #: How long an editor's preview link stays good for. Short: the link
    #: unlocks unpublished content to anyone holding it.
    PREVIEW_TOKEN_EXPIRE_MINUTES: int = 30

    # Attendance is measured in local wall-clock time — "did they check in
    # before 09:00" is meaningless in UTC. ED360 read this from
    # `Organization.timezone`; single-tenant it is deployment configuration.
    TIMEZONE: str = "Asia/Kathmandu"

    # Only ever touched when ENVIRONMENT=test (see uploads.py) — the hermetic
    # local-disk fallback so the test suite doesn't need real Cloudinary
    # credentials or network access. Unused in development/production.
    UPLOAD_DIR: str = str(BASE_DIR / "uploads")
    #: Where uploads go: `cloudinary`, or `local` (files under UPLOAD_DIR,
    #: served by this API). Unset means local in development and tests and
    #: Cloudinary everywhere else, so a developer's uploads never land in the
    #: production Cloudinary account. Production refuses `local`.
    STORAGE_BACKEND: Literal["cloudinary", "local"] | None = None
    #: This API's own address, as a browser reaches it. Local storage builds
    #: absolute file URLs from it, because the portals run on other origins.
    BACKEND_PUBLIC_URL: str = "http://localhost:8001"
    MAX_UPLOAD_SIZE_MB: int = Field(default=25, ge=1)
    #: Interview recordings (the one document type that takes video). Kept
    #: under MAX_REQUEST_BODY_MB, which would otherwise refuse the request
    #: first; anything bigger goes in as a link. Stored on Cloudinary's `raw`
    #: pipeline like every non-image, so the plan's raw-file limit applies too.
    MAX_VIDEO_UPLOAD_MB: int = Field(default=50, ge=1)
    #: Ceiling on a whole request body, enforced while it streams in
    #: (core/middleware.BodySizeLimitMiddleware) — before any multipart
    #: parsing or buffering. Covers a message carrying several attachments,
    #: which the per-file limit alone never bounded (FAPI-SEC-012).
    MAX_REQUEST_BODY_MB: int = Field(default=60, ge=1)

    # ── Private-file links (FAPI-SEC-013) ─────────────────────────────────────
    # Every private-file URL this API mints goes through Cloudinary's signed
    # download API with an `expires_at`, so it is valid for only
    # `CLOUDINARY_URL_TTL_SECONDS` — see `core/uploads.build_download_url`.
    # `CLOUDINARY_AUTH_TOKEN_KEY` is no longer needed for that; it is kept so an
    # existing .env that sets it still loads.
    CLOUDINARY_AUTH_TOKEN_KEY: str = Field(default="", repr=False)
    CLOUDINARY_URL_TTL_SECONDS: int = Field(default=300, ge=30, le=86400)

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        if self.ENVIRONMENT == "production":
            if self.SIMULATED_PAYMENTS:
                # FAPI-SEC-001: no gateway is integrated, so "simulated" in
                # production means "free". Refuse to boot rather than trust
                # that someone remembered to set it.
                raise ValueError("SIMULATED_PAYMENTS must be false in production — no payment gateway is integrated.")
            if not self.JWT_SECRET_KEY:
                raise ValueError("JWT_SECRET_KEY must be set in production.")
            if self.JWT_SECRET_KEY in _INSECURE_SECRETS:
                raise ValueError("JWT_SECRET_KEY is a known placeholder value — generate a real secret.")
            if len(self.JWT_SECRET_KEY) < 32:
                raise ValueError("JWT_SECRET_KEY must be at least 32 characters in production.")
            if self.STORAGE_BACKEND == "local":
                raise ValueError("STORAGE_BACKEND=local is for development; production stores files in Cloudinary.")
            if not (self.CLOUDINARY_CLOUD_NAME and self.CLOUDINARY_API_KEY and self.CLOUDINARY_API_SECRET):
                raise ValueError(
                    "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET must all be set in production."
                )
        elif not self.JWT_SECRET_KEY:
            # Ephemeral per-process secret: tokens simply don't survive a restart
            # locally, which is fine and is safer than shipping a shared default.
            object.__setattr__(self, "JWT_SECRET_KEY", secrets.token_urlsafe(48))
        return self

    #: libpq/psycopg-only connection params that asyncpg's connect() rejects
    #: outright (TypeError: unexpected keyword argument) if they reach it via
    #: the query string — Neon's copy-pasteable connection string includes both.
    _ASYNC_INCOMPATIBLE_QUERY_PARAMS = frozenset({"sslmode", "channel_binding"})

    def _build_database_url(self, driver: str, *, strip_async_incompatible_params: bool = False) -> str:
        """Render a Postgres URL for the given SQLAlchemy driver.

        When DATABASE_URL is set (Neon, Render Postgres, ...) it's rewritten
        onto the requested driver rather than parsed into the individual
        DB_* fields — except under ENVIRONMENT=test, which ignores it and
        always builds from DB_HOST/PORT/NAME. The test suite asserts DB_NAME
        ends in `_test` before it will run destructive schema operations
        (see conftest.py's `engine` fixture); honoring DATABASE_URL here would
        let a developer's real DATABASE_URL in `.env` silently point tests at
        a live database that check can't see.
        """
        if self.DATABASE_URL and self.ENVIRONMENT != "test":
            url = make_url(self.DATABASE_URL).set(drivername=f"postgresql+{driver}")
            if strip_async_incompatible_params:
                query = {k: v for k, v in url.query.items() if k not in self._ASYNC_INCOMPATIBLE_QUERY_PARAMS}
                url = url.set(query=query)
            return url.render_as_string(hide_password=False)
        return f"postgresql+{driver}://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @computed_field(repr=False)  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Sync URL — used by Alembic. psycopg understands `sslmode` natively."""
        return self._build_database_url("psycopg")

    @property
    def cors_origin_regex(self) -> str | None:
        if self.CORS_ORIGIN_REGEX:
            return self.CORS_ORIGIN_REGEX
        if self.ENVIRONMENT == "development":
            return (
                r"^https?://(localhost|127\.0\.0\.1|\[::1\]|192\.168\.\d{1,3}\.\d{1,3}"
                r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?$"
            )
        return None

    @computed_field(repr=False)  # type: ignore[prop-decorator]
    @property
    def async_database_url(self) -> str:
        """Async URL — used by the application."""
        return self._build_database_url("asyncpg", strip_async_incompatible_params=True)

    @computed_field(repr=False)  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        # Same hermeticity concern as DATABASE_URL above: a real REDIS_URL in
        # `.env` must not make the test suite read or write through a live
        # Redis instance.
        if self.REDIS_URL and self.ENVIRONMENT != "test":
            return self.REDIS_URL
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def upload_dir(self) -> Path:
        path = Path(self.UPLOAD_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def uses_local_storage(self) -> bool:
        if self.STORAGE_BACKEND is not None:
            return self.STORAGE_BACKEND == "local"
        return self.ENVIRONMENT in ("development", "test")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
