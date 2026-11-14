"""
RBAC model (T-021). Permission and MemberRole are defined in code, not the
database — changing the permission set or what a role grants is a deploy,
not a migration.

On top of the role, a member can be granted a few permissions one at a time
(Membership.granted_permissions, 2026-11): `effective_permissions` is what a
membership can do, and every gate asks it rather than ROLE_PERMISSIONS.
"""

from collections.abc import Iterable
from enum import StrEnum


class Permission(StrEnum):
    MEMBERS_READ = "members:read"
    MEMBERS_MANAGE = "members:manage"
    BILLING_READ = "billing:read"
    BILLING_MANAGE = "billing:manage"
    FILES_READ = "files:read"
    # Upload, and delete what you uploaded. FILES_MANAGE deletes anyone's.
    FILES_WRITE = "files:write"
    FILES_MANAGE = "files:manage"
    AUDIT_READ = "audit:read"
    SETTINGS_MANAGE = "settings:manage"


class MemberRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


ROLE_PERMISSIONS: dict[MemberRole, frozenset[Permission]] = {
    MemberRole.OWNER: frozenset(Permission),
    MemberRole.ADMIN: frozenset(
        {
            Permission.MEMBERS_READ,
            Permission.MEMBERS_MANAGE,
            Permission.BILLING_READ,
            Permission.FILES_READ,
            Permission.FILES_WRITE,
            Permission.FILES_MANAGE,
            Permission.AUDIT_READ,
            Permission.SETTINGS_MANAGE,
        }
    ),
    # FILES_WRITE is not here but granted: every new membership starts with
    # it (Membership.granted_permissions' default), and it can be taken away.
    MemberRole.MEMBER: frozenset({Permission.MEMBERS_READ, Permission.FILES_READ}),
}

# What can be granted to one member. Never members:manage, billing:manage or
# settings:manage: each of those lets the holder change who can do what, or
# what the organization pays, and that is what a role is for.
GRANTABLE_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.FILES_WRITE,
        Permission.FILES_MANAGE,
        Permission.BILLING_READ,
        Permission.AUDIT_READ,
    }
)
DEFAULT_GRANTS: tuple[Permission, ...] = (Permission.FILES_WRITE,)


def effective_permissions(role: MemberRole, grants: Iterable[str]) -> frozenset[Permission]:
    """The role's permissions plus the member's grants.

    A stored grant that is not grantable (a row written by hand, or a
    permission later taken off the grantable list) counts for nothing.
    """
    granted = {Permission(g) for g in grants if g in GRANTABLE_PERMISSIONS}
    return ROLE_PERMISSIONS[role] | granted
