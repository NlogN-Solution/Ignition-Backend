from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_development_generates_a_secret_when_none_is_set() -> None:
    settings = Settings(ENVIRONMENT="development", JWT_SECRET_KEY="", _env_file=None)

    assert len(settings.JWT_SECRET_KEY) >= 32


def test_production_rejects_a_missing_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set"):
        Settings(ENVIRONMENT="production", JWT_SECRET_KEY="", _env_file=None)


def test_production_rejects_ed360_placeholder_secret() -> None:
    """ED360 ships `change-me-super-secret` as its default. Copying that .env
    across is the exact mistake this guard exists to catch."""
    with pytest.raises(ValidationError, match="known placeholder"):
        Settings(ENVIRONMENT="production", JWT_SECRET_KEY="change-me-super-secret", _env_file=None)


def test_production_rejects_a_short_secret() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(ENVIRONMENT="production", JWT_SECRET_KEY="tooshort", _env_file=None)


def test_urls_use_the_right_drivers() -> None:
    settings = Settings(ENVIRONMENT="development", _env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://")  # alembic, sync
    assert settings.async_database_url.startswith("postgresql+asyncpg://")  # app, async
