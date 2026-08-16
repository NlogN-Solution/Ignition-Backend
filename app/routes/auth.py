from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..api.auth import get_current_user
from ..api.exceptions import BadRequestException, UnauthorizedException
from ..core.rate_limit import (
    LOGIN_RATE_LIMIT,
    PASSWORD_RATE_LIMIT,
    REFRESH_RATE_LIMIT,
    REGISTER_RATE_LIMIT,
    limiter,
)
from ..models import User
from ..models.enums import ActivityType
from ..schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    PublicRegisterRequest,
    RefreshRequest,
    TokenResponse,
)
from ..schemas.user import UserRead
from ..services.activity_log_service import ActivityLogService, get_activity_log_service
from ..services.auth_service import AuthService, get_auth_service

router = APIRouter(prefix="/auth", tags=["Auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login",
    description="Authenticate and return an access/refresh token pair.",
)
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(
    payload: LoginRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
) -> dict[str, str]:
    ip_address = _client_ip(request)
    user_agent = request.headers.get("user-agent")

    user = await auth_service.authenticate(payload.email, payload.password, ip_address)
    if user is None:
        await activity_log_service.log(
            user_id=None,
            activity_type=ActivityType.LOGIN,
            entity_type="user",
            description=f"Failed login attempt for {payload.email}",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise BadRequestException("Invalid email or password")

    await activity_log_service.log(
        user_id=user.id,
        activity_type=ActivityType.LOGIN,
        entity_type="user",
        entity_id=user.id,
        description=f"{user.email} logged in",
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return await auth_service.issue_tokens(user, ip_address=ip_address, user_agent=user_agent)


@router.post(
    "/register",
    response_model=TokenResponse,
    summary="Register",
    description=(
        "Public self-signup for the student portal. Always creates an active STUDENT — "
        "`role` and `status` are rejected if sent. Staff accounts come from POST /users."
    ),
)
@limiter.limit(REGISTER_RATE_LIMIT)
async def register(
    payload: PublicRegisterRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
) -> dict[str, str]:
    ip_address = _client_ip(request)
    user_agent = request.headers.get("user-agent")

    user = await auth_service.register_user(payload)
    await activity_log_service.log(
        user_id=user.id,
        activity_type=ActivityType.CREATE,
        entity_type="user",
        entity_id=user.id,
        description=f"{user.email} registered a student account",
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return await auth_service.issue_tokens(user, ip_address=ip_address, user_agent=user_agent)


@router.get("/me", response_model=UserRead, summary="Current user")
async def me(user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(user)


@router.post(
    "/change-password",
    response_model=UserRead,
    summary="Change password",
    description="Self-service password change. Revokes every other session.",
)
@limiter.limit(PASSWORD_RATE_LIMIT)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
) -> UserRead:
    changed = await auth_service.change_password(user, payload.current_password, payload.new_password)
    if not changed:
        raise BadRequestException("Current password is incorrect")

    await activity_log_service.log(
        user_id=user.id,
        activity_type=ActivityType.UPDATE,
        entity_type="user",
        entity_id=user.id,
        description=f"{user.email} changed their password",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return UserRead.model_validate(user)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh tokens",
    description="Exchange a refresh token for a new pair. The presented token is consumed.",
)
@limiter.limit(REFRESH_RATE_LIMIT)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    tokens = await auth_service.refresh_tokens(
        payload.refresh_token,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if tokens is None:
        raise UnauthorizedException("Invalid or expired refresh token")
    return tokens


@router.post(
    "/logout",
    summary="Logout",
    description=(
        "Revoke the supplied refresh token. Omit it to revoke every session for the "
        "current user. Unlike ED360's, this is a real server-side revocation."
    ),
)
async def logout(
    request: Request,
    payload: LogoutRequest | None = None,
    user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
) -> dict[str, bool]:
    if payload is not None and payload.refresh_token:
        await auth_service.revoke_session(payload.refresh_token)
    else:
        await auth_service.revoke_all_sessions(user.id)
        await auth_service.session.commit()

    await activity_log_service.log(
        user_id=user.id,
        activity_type=ActivityType.LOGOUT,
        entity_type="user",
        entity_id=user.id,
        description=f"{user.email} logged out",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"success": True}
