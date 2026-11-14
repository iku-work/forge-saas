"""
Verifies T-021's role -> permission map. The matrix (every MemberRole x every
Permission) is the contract that app.core.dependencies.require_permission
enforces — see tests/committed/test_dependencies.py for the dependency itself.
"""

import pytest

from app.modules.auth.permissions import (
    DEFAULT_GRANTS,
    GRANTABLE_PERMISSIONS,
    ROLE_PERMISSIONS,
    MemberRole,
    Permission,
    effective_permissions,
)


def test_owner_has_every_permission() -> None:
    assert ROLE_PERMISSIONS[MemberRole.OWNER] == frozenset(Permission)


def test_admin_has_every_permission_except_billing_manage() -> None:
    assert ROLE_PERMISSIONS[MemberRole.ADMIN] == frozenset(Permission) - {Permission.BILLING_MANAGE}


def test_member_can_only_read_members_and_files() -> None:
    assert ROLE_PERMISSIONS[MemberRole.MEMBER] == frozenset(
        {Permission.MEMBERS_READ, Permission.FILES_READ}
    )


def test_every_role_has_an_entry() -> None:
    assert set(ROLE_PERMISSIONS) == set(MemberRole)


@pytest.mark.owasp("A01")
@pytest.mark.parametrize("role", list(MemberRole))
@pytest.mark.parametrize("permission", list(Permission))
def test_rbac_matrix_matches_expected_table(role: MemberRole, permission: Permission) -> None:
    expected = {
        MemberRole.OWNER: True,
        MemberRole.ADMIN: permission != Permission.BILLING_MANAGE,
        MemberRole.MEMBER: permission in {Permission.MEMBERS_READ, Permission.FILES_READ},
    }[role]

    assert (permission in ROLE_PERMISSIONS[role]) is expected


def test_only_these_can_be_granted_to_one_member() -> None:
    """Never members:manage, billing:manage or settings:manage: each lets the
    holder change who can do what, or what the organization pays."""
    assert {
        Permission.FILES_WRITE,
        Permission.FILES_MANAGE,
        Permission.BILLING_READ,
        Permission.AUDIT_READ,
    } == GRANTABLE_PERMISSIONS
    assert set(DEFAULT_GRANTS) <= GRANTABLE_PERMISSIONS


def test_grants_add_to_the_role() -> None:
    assert effective_permissions(MemberRole.MEMBER, ["files:write", "audit:read"]) == {
        Permission.MEMBERS_READ,
        Permission.FILES_READ,
        Permission.FILES_WRITE,
        Permission.AUDIT_READ,
    }


@pytest.mark.owasp("A01")
def test_a_stored_grant_that_is_not_grantable_counts_for_nothing() -> None:
    """A row written by hand, or a permission later taken off the list."""
    assert (
        effective_permissions(
            MemberRole.MEMBER, ["members:manage", "settings:manage", "not-a-permission"]
        )
        == ROLE_PERMISSIONS[MemberRole.MEMBER]
    )
