"""
RBAC model (T-021). Permission and MemberRole are defined in code, not the
database — changing the permission set or what a role grants is a deploy,
not a migration. app.core.dependencies.require_permission is the only place
that should consult ROLE_PERMISSIONS to gate a route.
"""

from enum import StrEnum


class Permission(StrEnum):
    MEMBERS_READ = "members:read"
    MEMBERS_MANAGE = "members:manage"
    BILLING_READ = "billing:read"
    BILLING_MANAGE = "billing:manage"
    FILES_READ = "files:read"
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
            Permission.FILES_MANAGE,
            Permission.AUDIT_READ,
            Permission.SETTINGS_MANAGE,
        }
    ),
    MemberRole.MEMBER: frozenset({Permission.MEMBERS_READ, Permission.FILES_READ}),
}
