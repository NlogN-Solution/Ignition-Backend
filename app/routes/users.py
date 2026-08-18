from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile

from ..api.auth import get_current_user, require_role
from ..api.exceptions import BadRequestException, ForbiddenException, NotFoundException
from ..core.config import get_settings
from ..core.rbac import can_manage_target
from ..core.uploads import AVATAR_EXTENSIONS, AVATAR_FOLDER, store_upload
from ..models import User
from ..models.enums import ActivityType, UserRole
from ..schemas.user import (
    ResetPasswordRequest,
    ResetPasswordResponse,
    StaffDirectoryEntry,
    StaffDirectoryList,
    UserCreate,
    UserList,
    UserRead,
    UserSelfUpdate,
    UserUpdate,
)
from ..services.activity_log_service import ActivityLogService, get_activity_log_service
from ..services.user_service import UserService, get_user_service

settings = get_settings()

router = APIRouter(prefix="/users", tags=["Users"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _store_avatar(file: UploadFile) -> str:
    """Persist an avatar to Cloudinary and return its public, CDN-served URL.

    Avatars are uploaded publicly: they are low-sensitivity and are rendered by
    <img> tags all over the dashboard. Documents deliberately are not — see
    `routes/document.py:download_document`.
    """
    stored = await store_upload(file, AVATAR_EXTENSIONS, folder=AVATAR_FOLDER)
    assert stored.url is not None  # public upload (private=False, the default): Cloudinary always returns a URL
    return stored.url


@router.get("", response_model=UserList, summary="List users")
async def list_users(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    role: str | None = None,
    status: str | None = None,
    deleted: bool = False,
    user_service: UserService = Depends(get_user_service),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER, UserRole.COUNSELLOR)),
) -> UserList:
    # Counsellors browse applicants day-to-day but must not see staff or
    # soft-deleted accounts — pin them to active students regardless of the
    # query string they send.
    if user.role is UserRole.COUNSELLOR:
        role = UserRole.STUDENT.value
        deleted = False

    users, total = await user_service.list_users(
        page=page,
        limit=limit,
        search=search,
        role=role,
        status=status,
        deleted=deleted,
    )
    return UserList(
        items=[UserRead.model_validate(u) for u in users],
        total=total,
        page=page,
        limit=limit,
    )


@router.patch("/me", response_model=UserRead, summary="Update my own profile")
async def update_my_profile(
    payload: UserSelfUpdate,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user),
) -> UserRead:
    user = await user_service.update_user(current_user, payload.model_dump(exclude_unset=True))
    return UserRead.model_validate(user)


@router.post("/me/avatar", response_model=UserRead, summary="Upload my avatar")
async def upload_my_avatar(
    file: UploadFile = File(...),
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user),
) -> UserRead:
    avatar_url = await _store_avatar(file)
    user = await user_service.update_user(current_user, {"avatar_url": avatar_url})
    return UserRead.model_validate(user)


# Registered before `/{user_id}` so this literal path always wins.
@router.get(
    "/staff-directory",
    response_model=StaffDirectoryList,
    summary="Search staff for assigning as appointment attendees",
)
async def list_staff_directory(
    search: str | None = None,
    role: str | None = None,
    user_id: UUID | None = None,
    limit: int = 20,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user),
) -> StaffDirectoryList:
    """Open to every authenticated role, including students — but for a student
    it resolves one id at a time.

    ED360 spells out all twelve roles in a `require_role` call, which means a
    role added later silently loses access. `get_current_user` plus the student
    branch below expresses the same rule without that failure mode.
    """
    if current_user.role is UserRole.STUDENT:
        # A student may put a name to a staff id already on their own data
        # (their appointment's counsellor, say) — never browse the roster.
        if user_id is None:
            raise ForbiddenException("Forbidden")
        search = None
        role = None

    users = await user_service.list_staff_directory(search=search, role=role, user_id=user_id, limit=limit)
    return StaffDirectoryList(items=[StaffDirectoryEntry.model_validate(u) for u in users])


@router.get("/{user_id}", response_model=UserRead, summary="Get user")
async def get_user(
    user_id: UUID,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.MANAGER)),
) -> UserRead:
    target = await user_service.get_user(user_id)
    if target is None:
        raise NotFoundException("User not found")
    if current_user.role is UserRole.COUNSELLOR and target.role is not UserRole.STUDENT:
        raise ForbiddenException("Forbidden")
    return UserRead.model_validate(target)


@router.post("", response_model=UserRead, summary="Create user")
async def create_user(
    payload: UserCreate,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> UserRead:
    """The only way a staff account comes into existence.

    Public `/auth/register` cannot set a role, so every privileged account
    passes through this `can_manage_target` check.
    """
    if not can_manage_target(current_user.role.value, payload.role.value, payload.role.value):
        raise ForbiddenException("You cannot create a user with that role")

    if await user_service.get_by_email(payload.email):
        raise BadRequestException("An account with this email already exists")

    user, _generated = await user_service.create_user(payload.model_dump())

    await activity_log_service.log(
        user_id=current_user.id,
        activity_type=ActivityType.CREATE,
        entity_type="user",
        entity_id=user.id,
        description=f"Created user {user.email} with role {user.role.value}",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return UserRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserRead, summary="Update user")
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR)),
) -> UserRead:
    user = await user_service.get_user(user_id)
    if user is None:
        raise NotFoundException("User not found")

    # Counsellors may fill in an applicant's details, but cannot touch staff
    # accounts or move anyone between roles and statuses.
    if current_user.role is UserRole.COUNSELLOR:
        if user.role is not UserRole.STUDENT:
            raise ForbiddenException("You do not have permission to modify this user")
        if payload.role is not None or payload.status is not None:
            raise ForbiddenException("Only an admin can change a user's role or status")

    new_role = payload.role.value if payload.role else None
    if not can_manage_target(current_user.role.value, user.role.value, new_role):
        raise ForbiddenException("You do not have permission to modify this user")

    data = payload.model_dump(exclude_unset=True)
    previous_role = user.role
    role_changed = payload.role is not None and payload.role is not previous_role

    user = await user_service.update_user(user, data)

    await activity_log_service.log(
        user_id=current_user.id,
        activity_type=ActivityType.UPDATE,
        entity_type="user",
        entity_id=user.id,
        description=(
            f"Changed role of {user.email} from {previous_role.value} to {user.role.value}"
            if role_changed
            else f"Updated user {user.email}"
        ),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return UserRead.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse, summary="Reset user password")
async def reset_password(
    user_id: UUID,
    payload: ResetPasswordRequest,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> ResetPasswordResponse:
    user = await user_service.get_user(user_id)
    if user is None:
        raise NotFoundException("User not found")
    if not can_manage_target(current_user.role.value, user.role.value):
        raise ForbiddenException("You do not have permission to reset this user's password")

    user, generated_password = await user_service.reset_password(user, payload.new_password)

    await activity_log_service.log(
        user_id=current_user.id,
        activity_type=ActivityType.UPDATE,
        entity_type="user",
        entity_id=user.id,
        description=f"Reset password for {user.email}",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ResetPasswordResponse(user_id=user.id, generated_password=generated_password)


@router.post(
    "/{user_id}/enable-portal",
    response_model=ResetPasswordResponse,
    summary="Enable a student's portal login",
)
async def enable_student_portal(
    user_id: UUID,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR)),
) -> ResetPasswordResponse:
    """A narrower sibling of `/reset-password`: what a counsellor uses to turn
    on login for a student converted without portal access.

    Student accounts only, and only while no password is set, so it cannot be
    turned into a password reset for an account someone already uses.
    """
    user = await user_service.get_user(user_id)
    if user is None:
        raise NotFoundException("User not found")
    if user.role is not UserRole.STUDENT:
        raise ForbiddenException("Portal access can only be enabled for student accounts")
    if user.has_portal_access:
        raise BadRequestException("This student already has portal access")

    user, generated_password = await user_service.reset_password(user, None)

    await activity_log_service.log(
        user_id=current_user.id,
        activity_type=ActivityType.UPDATE,
        entity_type="user",
        entity_id=user.id,
        description=f"Enabled student portal access for {user.email}",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ResetPasswordResponse(user_id=user.id, generated_password=generated_password)


@router.delete("/{user_id}", response_model=UserRead, summary="Delete user")
async def delete_user(
    user_id: UUID,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> UserRead:
    user = await user_service.get_user(user_id)
    if user is None:
        raise NotFoundException("User not found")
    if user.id == current_user.id:
        raise BadRequestException("You cannot deactivate your own account")
    if not can_manage_target(current_user.role.value, user.role.value):
        raise ForbiddenException("You do not have permission to deactivate this user")

    user = await user_service.delete_user(user)

    await activity_log_service.log(
        user_id=current_user.id,
        activity_type=ActivityType.DELETE,
        entity_type="user",
        entity_id=user.id,
        description=f"Deactivated user {user.email}",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return UserRead.model_validate(user)


@router.post("/{user_id}/restore", response_model=UserRead, summary="Restore deleted user")
async def restore_user(
    user_id: UUID,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    activity_log_service: ActivityLogService = Depends(get_activity_log_service),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> UserRead:
    user = await user_service.get_user(user_id, include_deleted=True)
    if user is None:
        raise NotFoundException("User not found")
    if not can_manage_target(current_user.role.value, user.role.value):
        raise ForbiddenException("You do not have permission to reactivate this user")

    user = await user_service.restore_user(user)

    await activity_log_service.log(
        user_id=current_user.id,
        activity_type=ActivityType.UPDATE,
        entity_type="user",
        entity_id=user.id,
        description=f"Reactivated user {user.email}",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return UserRead.model_validate(user)


@router.post(
    "/{user_id}/avatar",
    response_model=UserRead,
    summary="Upload a user's avatar (staff, on an applicant's behalf)",
)
async def upload_user_avatar(
    user_id: UUID,
    file: UploadFile = File(...),
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR)),
) -> UserRead:
    user = await user_service.get_user(user_id)
    if user is None:
        raise NotFoundException("User not found")
    if current_user.role is UserRole.COUNSELLOR and user.role is not UserRole.STUDENT:
        raise ForbiddenException("You do not have permission to modify this user")

    avatar_url = await _store_avatar(file)
    user = await user_service.update_user(user, {"avatar_url": avatar_url})
    return UserRead.model_validate(user)
