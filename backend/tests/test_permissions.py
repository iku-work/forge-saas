"""
Verifies T-021's role -> permission map. The matrix (every MemberRole x every
Permission) is the contract that app.core.dependencies.require_permission
enforces — see tests/test_dependencies.py for the dependency itself.
"""

import pytest

from app.modules.auth.permissions import ROLE_PERMISSIONS, MemberRole, Permission


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


@pytest.mark.parametrize("role", list(MemberRole))
@pytest.mark.parametrize("permission", list(Permission))
def test_rbac_matrix_matches_expected_table(role: MemberRole, permission: Permission) -> None:
    expected = {
        MemberRole.OWNER: True,
        MemberRole.ADMIN: permission != Permission.BILLING_MANAGE,
        MemberRole.MEMBER: permission in {Permission.MEMBERS_READ, Permission.FILES_READ},
    }[role]

    assert (permission in ROLE_PERMISSIONS[role]) is expected
