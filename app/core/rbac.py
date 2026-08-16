from __future__ import annotations

from ..models.enums import UserRole

#: Higher rank may manage lower rank. Ranks are compared, never summed — the
#: numbers are ordering only.
ROLE_RANK: dict[str, int] = {
    UserRole.STUDENT.value: 0,
    # ED360 leaves VIEWER out of this map entirely and relies on `.get(role, 0)`
    # to default it. Spelling it out means a typo'd role name is the only thing
    # that can silently fall through to rank 0.
    UserRole.VIEWER.value: 0,
    UserRole.COUNSELLOR.value: 1,
    UserRole.FRONTDESK.value: 1,
    UserRole.STAFF.value: 1,
    UserRole.FINANCE.value: 1,
    UserRole.MARKETING.value: 1,
    UserRole.SUPPORT.value: 1,
    UserRole.ADMISSIONS.value: 1,
    UserRole.MANAGER.value: 2,
    UserRole.ADMIN.value: 3,
    UserRole.SUPER_ADMIN.value: 4,
}


def can_manage_target(
    acting_role: str,
    target_role: str,
    new_role: str | None = None,
) -> bool:
    """Whether `acting_role` may create/update/delete a user holding
    `target_role`, optionally reassigning them to `new_role`.

    super_admin is the platform owner and can manage anyone. Everyone else needs
    a strictly higher rank than both the target's current role and the role
    being assigned — the second check is what stops an admin from promoting
    someone (or themselves) to super_admin.

    ED360's `acting_is_platform_admin` bypass is gone (strip rule R3): there is
    no platform above this single tenant, so super_admin is the top of the
    ladder.
    """
    if acting_role == UserRole.SUPER_ADMIN.value:
        return True

    # Strictly higher than the target's current role, and than the role being
    # assigned when one is — the second is what stops sideways promotion.
    ranks_to_clear = [ROLE_RANK.get(target_role, 0)]
    if new_role is not None:
        ranks_to_clear.append(ROLE_RANK.get(new_role, 0))

    return ROLE_RANK.get(acting_role, 0) > max(ranks_to_clear)
