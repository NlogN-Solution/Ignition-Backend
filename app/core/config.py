from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6380
    REDIS_DB: int = 0

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
        elif not self.JWT_SECRET_KEY:
            # Ephemeral per-process secret: tokens simply don't survive a restart
            # locally, which is fine and is safer than shipping a shared default.
            object.__setattr__(self, "JWT_SECRET_KEY", secrets.token_urlsafe(48))
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Sync URL — used by Alembic."""
        return f"postgresql+psycopg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_database_url(self) -> str:
        """Async URL — used by the application."""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
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
