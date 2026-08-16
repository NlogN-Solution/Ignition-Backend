from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Point every test at a dedicated database before anything imports settings, so
# a test run can never touch dev data. Created by scripts/init-test-db.sql.
os.environ.setdefault("DB_NAME", "ignition_test")
os.environ.setdefault("ENVIRONMENT", "test")

from app.api.deps import get_db_session  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import *  # noqa: E402,F401,F403  (registers models on Base.metadata)


@pytest_asyncio.fixture(scope="session")
async def engine():
    """One engine for the whole run; schema is built once and dropped at the end."""
    settings = get_settings()
    assert settings.DB_NAME.endswith("_test"), (
        f"Refusing to run tests against {settings.DB_NAME!r} — the test database name must end in '_test'."
    )

    # NullPool is load-bearing, not a performance choice. asyncpg binds a
    # connection to the event loop that opened it, and pytest-asyncio runs each
    # test on its own loop while this fixture lives on the session loop. A
    # pooled connection would therefore be handed to a test on a different loop
    # and fail with "attached to a different loop". NullPool opens a fresh
    # connection per checkout, always on the current loop.
    engine = create_async_engine(settings.async_database_url, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def connection(engine) -> AsyncGenerator[AsyncConnection, None]:
    """A connection wrapped in a transaction that is always rolled back.

    Every test therefore starts from the same empty schema without paying for a
    create/drop cycle per test.
    """
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


@pytest_asyncio.fixture
async def session(connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """Session bound to the rolled-back connection.

    `join_transaction_mode="create_savepoint"` lets application code call
    `session.commit()` normally — the commit lands on a savepoint inside the
    outer transaction, which the fixture then discards.
    """
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client whose requests share the test's session and rollback."""

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    fastapi_app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ── Factories ─────────────────────────────────────────────────────────────────

DEFAULT_PASSWORD = "correct-horse-battery"


@pytest_asyncio.fixture
async def user_factory(session: AsyncSession):
    """Create a persisted user. Defaults to an active student."""
    from app.core.security import hash_password
    from app.models import User
    from app.models.enums import UserRole, UserStatus

    created = 0

    async def _make(
        role: UserRole = UserRole.STUDENT,
        *,
        email: str | None = None,
        password: str | None = DEFAULT_PASSWORD,
        status: UserStatus = UserStatus.ACTIVE,
        **kwargs,
    ) -> User:
        nonlocal created
        created += 1
        user = User(
            email=email or f"{role.value}{created}@example.com",
            password_hash=hash_password(password) if password else None,
            first_name=role.value.title(),
            last_name=f"User{created}",
            role=role,
            status=status,
            **kwargs,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    return _make


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient):
    """Log a user in over HTTP and return their bearer header.

    Deliberately goes through `/auth/login` rather than minting a token
    directly, so tests exercise the same path real clients do — including the
    session row that refresh and logout depend on.
    """

    async def _headers(user, password: str = DEFAULT_PASSWORD) -> dict[str, str]:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": password},
        )
        assert response.status_code == 200, f"login failed for {user.email}: {response.text}"
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _headers
