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

    # ── Database ──────────────────────────────────────────────────────────────
    # Local dev composes a URL from the individual fields below. A hosted
    # Postgres (Neon, Render Postgres, ...) instead hands out one connection
    # string — set DATABASE_URL and it overrides DB_HOST/PORT/NAME/USER/PASSWORD
    # entirely rather than needing them parsed apart.
    DATABASE_URL: str | None = None
    DB_HOST: str = "localhost"
    DB_PORT: int = 5433
    DB_NAME: str = "ignition"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_POOL_SIZE: int = 5
    DB_ECHO: bool = False

    # ── Auth ──────────────────────────────────────────────────────────────────
    # No default. In development one is generated per process (see the validator
    # below) so `docker compose up` works out of the box; in production a missing
    # or weak secret is a hard failure.
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Brute-force lockout. ED360's `users` table models both columns and never
    # writes either; `AuthService.authenticate` enforces them here.
    MAX_FAILED_LOGIN_ATTEMPTS: int = 5
    ACCOUNT_LOCKOUT_MINUTES: int = 15

    # ── Redis ─────────────────────────────────────────────────────────────────
    # Same override pattern as DATABASE_URL — a managed Redis (Render Key Value,
    # etc.) hands out one connection string rather than host/port pieces.
    REDIS_URL: str | None = None
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6380
    REDIS_DB: int = 0

    # ── Cloudinary ────────────────────────────────────────────────────────────
    # Media (avatars, documents, leave attachments) is stored on Cloudinary, not
    # on local/ephemeral disk — see core/uploads.py. Required in production;
    # ENVIRONMENT=test never calls Cloudinary (see uploads.py), so these stay
    # blank in the test suite.
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # ── HTTP ──────────────────────────────────────────────────────────────────
    # 5174 = admin dashboard (Vite), 3000 = student portal (CRA).
    # ED360's own stack occupies 5173/8000 on this machine, hence the offsets.
    CORS_ORIGINS: list[str] = Field(
        default=[
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )

    # Attendance is measured in local wall-clock time — "did they check in
    # before 09:00" is meaningless in UTC. ED360 read this from
    # `Organization.timezone`; single-tenant it is deployment configuration.
    TIMEZONE: str = "Asia/Kathmandu"

    # Only ever touched when ENVIRONMENT=test (see uploads.py) — the hermetic
    # local-disk fallback so the test suite doesn't need real Cloudinary
    # credentials or network access. Unused in development/production.
    UPLOAD_DIR: str = str(BASE_DIR / "uploads")
    MAX_UPLOAD_SIZE_MB: int = 25

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        if self.ENVIRONMENT == "production":
            if not self.JWT_SECRET_KEY:
                raise ValueError("JWT_SECRET_KEY must be set in production.")
            if self.JWT_SECRET_KEY in _INSECURE_SECRETS:
                raise ValueError("JWT_SECRET_KEY is a known placeholder value — generate a real secret.")
            if len(self.JWT_SECRET_KEY) < 32:
                raise ValueError("JWT_SECRET_KEY must be at least 32 characters in production.")
            if not (self.CLOUDINARY_CLOUD_NAME and self.CLOUDINARY_API_KEY and self.CLOUDINARY_API_SECRET):
                raise ValueError(
                    "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET must all be set "
                    "in production."
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Sync URL — used by Alembic. psycopg understands `sslmode` natively."""
        return self._build_database_url("psycopg")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_database_url(self) -> str:
        """Async URL — used by the application."""
        return self._build_database_url("asyncpg", strip_async_incompatible_params=True)

    @computed_field  # type: ignore[prop-decorator]
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
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
