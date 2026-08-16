"""Auth endpoint tests.

The Phase 2 section of IGNITION_PLATFORM_PLAN.md lists four exploitable bugs in
ED360's auth that a naive port would inherit. Each has a regression test here,
marked with the issue it pins down.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import User, UserSession
from app.models.enums import UserRole, UserStatus
from tests.conftest import DEFAULT_PASSWORD

pytestmark = pytest.mark.asyncio

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
CHANGE_PASSWORD = "/api/v1/auth/change-password"


def _registration(**overrides) -> dict:
    payload = {
        "email": "applicant@example.com",
        "password": "a-good-password",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
    payload.update(overrides)
    return payload


# ── Registration ──────────────────────────────────────────────────────────────


async def test_register_creates_an_active_student(client: AsyncClient, session: AsyncSession) -> None:
    response = await client.post(REGISTER, json=_registration())
    assert response.status_code == 200, response.text
    assert response.json()["token_type"] == "bearer"

    user = await session.scalar(select(User).where(User.email == "applicant@example.com"))
    assert user is not None
    assert user.role is UserRole.STUDENT
    assert user.status is UserStatus.ACTIVE


@pytest.mark.parametrize("escalation", [{"role": "super_admin"}, {"role": "admin"}, {"status": "suspended"}])
async def test_register_rejects_client_supplied_role_or_status(
    client: AsyncClient,
    session: AsyncSession,
    escalation: dict,
) -> None:
    """ED360 bug 1: `POST /auth/register` passes the body's `role` straight to
    the User constructor, so anyone can register as super_admin.

    Ignition rejects the field outright rather than ignoring it — a caller
    attempting escalation gets a 422, not a silently downgraded account.
    """
    response = await client.post(REGISTER, json=_registration(**escalation))

    assert response.status_code == 422, response.text
    assert await session.scalar(select(User).where(User.email == "applicant@example.com")) is None


async def test_register_rejects_duplicate_email(client: AsyncClient) -> None:
    assert (await client.post(REGISTER, json=_registration())).status_code == 200
    duplicate = await client.post(REGISTER, json=_registration())
    assert duplicate.status_code == 409


# ── Login ─────────────────────────────────────────────────────────────────────


async def test_login_returns_tokens_and_records_a_session(
    client: AsyncClient,
    session: AsyncSession,
    user_factory,
) -> None:
    user = await user_factory(UserRole.COUNSELLOR)
    response = await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"] and body["refresh_token"]

    sessions = (await session.scalars(select(UserSession).where(UserSession.user_id == user.id))).all()
    assert len(sessions) == 1
    assert sessions[0].revoked_at is None
    # The raw token is never stored.
    assert sessions[0].refresh_token_hash != body["refresh_token"]


async def test_login_rejects_a_wrong_password(client: AsyncClient, user_factory) -> None:
    user = await user_factory()
    response = await client.post(LOGIN, json={"email": user.email, "password": "not-the-password"})
    assert response.status_code == 400


async def test_login_locks_the_account_after_repeated_failures(
    client: AsyncClient,
    session: AsyncSession,
    user_factory,
) -> None:
    """ED360 bug 3: `failed_login_attempts` and `locked_until` are modelled and
    never written, so the lockout is decorative."""
    settings = get_settings()
    user = await user_factory()

    for _ in range(settings.MAX_FAILED_LOGIN_ATTEMPTS):
        failed = await client.post(LOGIN, json={"email": user.email, "password": "wrong-password"})
        assert failed.status_code == 400

    await session.refresh(user)
    assert user.failed_login_attempts == settings.MAX_FAILED_LOGIN_ATTEMPTS
    assert user.locked_until is not None

    # The correct password is now refused too — that is the point of a lockout.
    locked = await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    assert locked.status_code == 403


async def test_a_successful_login_clears_the_failure_counter(
    client: AsyncClient,
    session: AsyncSession,
    user_factory,
) -> None:
    user = await user_factory()
    await client.post(LOGIN, json={"email": user.email, "password": "wrong-password"})
    await session.refresh(user)
    assert user.failed_login_attempts == 1

    assert (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).status_code == 200
    await session.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None


async def test_suspended_users_cannot_log_in(client: AsyncClient, user_factory) -> None:
    user = await user_factory(status=UserStatus.SUSPENDED)
    response = await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    # Credentials are valid, so authentication succeeds and the status gate
    # rejects it — a 403 rather than a 400.
    assert response.status_code == 403


# ── Refresh + logout: the revocation fix ──────────────────────────────────────


async def test_refresh_rotates_and_consumes_the_old_token(client: AsyncClient, user_factory) -> None:
    """ED360 bug 2, part 1: refresh tokens are re-usable for their full 30 days."""
    user = await user_factory()
    login = await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    original = login.json()["refresh_token"]

    first = await client.post(REFRESH, json={"refresh_token": original})
    assert first.status_code == 200
    assert first.json()["refresh_token"] != original

    replayed = await client.post(REFRESH, json={"refresh_token": original})
    assert replayed.status_code == 401, "a consumed refresh token must not work twice"


async def test_logout_revokes_the_refresh_token(client: AsyncClient, user_factory) -> None:
    """ED360 bug 2, part 2: logout only logs an activity row, so a stolen
    refresh token outlives it."""
    user = await user_factory()
    login = await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    tokens = login.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    logout = await client.post(LOGOUT, headers=headers, json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 200

    after = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert after.status_code == 401


async def test_changing_a_password_revokes_every_session(client: AsyncClient, user_factory) -> None:
    """The response to a suspected compromise must not leave the attacker's
    refresh token live."""
    user = await user_factory()
    first_login = (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).json()
    second_login = (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).json()

    changed = await client.post(
        CHANGE_PASSWORD,
        headers={"Authorization": f"Bearer {first_login['access_token']}"},
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a-brand-new-password"},
    )
    assert changed.status_code == 200, changed.text

    for tokens in (first_login, second_login):
        replay = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
        assert replay.status_code == 401


async def test_refresh_rejects_a_forged_or_unknown_token(client: AsyncClient) -> None:
    assert (await client.post(REFRESH, json={"refresh_token": "not-a-jwt"})).status_code == 401


async def test_an_access_token_is_not_accepted_as_a_refresh_token(client: AsyncClient, user_factory) -> None:
    user = await user_factory()
    tokens = (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).json()
    response = await client.post(REFRESH, json={"refresh_token": tokens["access_token"]})
    assert response.status_code == 401


# ── Bearer handling ───────────────────────────────────────────────────────────


async def test_me_returns_the_current_user(client: AsyncClient, user_factory, auth_headers) -> None:
    user = await user_factory(UserRole.ADMIN)
    response = await client.get(ME, headers=await auth_headers(user))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == user.email
    assert body["role"] == UserRole.ADMIN.value
    # Tenancy fields must not reappear in the response contract.
    assert "organization_id" not in body
    assert "is_platform_admin" not in body


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get(ME)).status_code == 401


async def test_a_malformed_token_is_401_not_500(client: AsyncClient) -> None:
    """ED360 lets `PyJWTError` escape `get_current_user`, so a garbled token is
    a 500 and a traceback."""
    response = await client.get(ME, headers={"Authorization": "Bearer garbage.token.here"})
    assert response.status_code == 401


async def test_a_refresh_token_is_not_accepted_as_a_bearer_token(client: AsyncClient, user_factory) -> None:
    user = await user_factory()
    tokens = (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).json()
    response = await client.get(ME, headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert response.status_code == 401
