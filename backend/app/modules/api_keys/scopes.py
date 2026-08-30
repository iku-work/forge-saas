"""
The public API's scope vocabulary (T-311), in one file.

A scope is the *public* name for a door. A `Permission`
(app.modules.auth.permissions) is the internal name for the same door as a
session sees it. The two vocabularies are deliberately separate:

  * Scopes are a published contract. They appear in a key's `scopes` array,
    in the management UI's checkboxes, and in the API documentation, and
    renaming one breaks every integration that stored it.
  * Permissions are internal and change with the RBAC matrix, which is code
    and therefore a deploy rather than a migration.

`SCOPE_PERMISSIONS` below is the mapping, and it is what makes a
`require_scope` route dual-principal: an API key is checked against its
scopes, a session against the permission the same scope maps to, and neither
gets in through the other's door. tests/modules/test_api_key_scopes.py fails
the build on a scope referenced by a `require_scope` that is not declared
here, and on a member of this enum with no permission mapped to it.

**There is no `org:read` permission and none was added.** Reading the
organization record has always been gated by membership alone, and
`MEMBERS_READ` is the permission every role already holds -- so mapping
`orgs:read` onto it opens exactly the door a session of any role can already
walk through, and adding a new permission would have meant changing the RBAC
matrix (and the test that pins it) to express something the matrix already
says.
"""

from enum import StrEnum

from app.modules.auth.permissions import Permission


class Scope(StrEnum):
    """Every scope a key may hold. A closed set, on purpose: an unknown scope
    on a create request is a 422, not a key that silently grants nothing."""

    ORGS_READ = "orgs:read"
    MEMBERS_READ = "members:read"
    MEMBERS_WRITE = "members:write"
    FILES_READ = "files:read"


# Scope -> the Permission a *session* needs to pass the same gate. Every
# member of Scope has an entry; the test above fails if one is ever added
# without.
SCOPE_PERMISSIONS: dict[Scope, Permission] = {
    Scope.ORGS_READ: Permission.MEMBERS_READ,
    Scope.MEMBERS_READ: Permission.MEMBERS_READ,
    Scope.MEMBERS_WRITE: Permission.MEMBERS_MANAGE,
    Scope.FILES_READ: Permission.FILES_READ,
}


def parse_scopes(values: list[str]) -> list[Scope]:
    """The requested scopes as members of the vocabulary, or a ValueError
    naming the first one that is not.

    Deliberately not a pydantic `list[Scope]` on the request schema: a bad
    scope there produces FastAPI's enum-validation message, which lists the
    whole vocabulary as an anonymous array. The service raises a
    DomainError instead, so the caller is told which value was wrong."""
    parsed = []
    for value in values:
        try:
            parsed.append(Scope(value))
        except ValueError as exc:
            raise ValueError(value) from exc
    return parsed


ALL_SCOPES: tuple[str, ...] = tuple(scope.value for scope in Scope)
