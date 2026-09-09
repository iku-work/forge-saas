"""
The scope vocabulary (T-311, and the half of T-314 that has to exist before
`require_scope` can be written at all).

Three properties, each of which is a way the vocabulary could rot silently:

  * every scope a `require_scope` names is declared -- otherwise a typo gates
    a route on a string nothing can ever hold, which reads as "locked down"
    and is actually "unreachable";
  * every declared scope maps to a Permission -- otherwise a session can
    never pass a gate an API key can, and the dual-principal rule quietly
    becomes key-only;
  * the mapping never grants more than the product already grants. A scope
    that mapped onto a Permission a role does not have would widen the API
    key's reach past what the person who minted it could do themselves.
"""

import pytest
from fastapi.routing import iter_route_contexts

from app.core.principal import declared_scope
from app.main import app
from app.modules.api_keys.scopes import ALL_SCOPES, SCOPE_PERMISSIONS, Scope, parse_scopes
from app.modules.auth.permissions import ROLE_PERMISSIONS, MemberRole, Permission


def _scopes_on_routes() -> set[str]:
    found: set[str] = set()

    def walk(dependant: object) -> None:
        for dependency in getattr(dependant, "dependencies", ()):
            scope = declared_scope(dependency.call)
            if scope is not None:
                found.add(scope)
            walk(dependency)

    for ctx in iter_route_contexts(app.routes):
        walk(getattr(ctx, "dependant", None))
    return found


def test_every_scope_used_by_a_route_is_declared() -> None:
    undeclared = _scopes_on_routes() - set(ALL_SCOPES)

    assert not undeclared, (
        f"routes gate on scopes that are not in the vocabulary: {sorted(undeclared)}. "
        "Add them to app.modules.api_keys.scopes.Scope, or fix the typo -- a gate on "
        "a scope nobody can hold is an endpoint nobody can reach."
    )


def test_every_declared_scope_maps_to_a_permission() -> None:
    assert set(SCOPE_PERMISSIONS) == set(Scope)


def test_no_scope_grants_more_than_an_owner_has() -> None:
    """The ceiling. A key is minted by a person, and cannot outrank the most
    privileged role the product has."""
    owner_permissions = ROLE_PERMISSIONS[MemberRole.OWNER]

    assert set(SCOPE_PERMISSIONS.values()) <= owner_permissions


def test_the_read_scopes_map_onto_read_permissions() -> None:
    """A vocabulary whose `:read` scope mapped onto a `:manage` permission
    would be a privilege escalation hiding behind a name."""
    write_permissions = {
        Permission.MEMBERS_MANAGE,
        Permission.BILLING_MANAGE,
        Permission.FILES_MANAGE,
        Permission.SETTINGS_MANAGE,
    }
    for scope, permission in SCOPE_PERMISSIONS.items():
        if scope.value.endswith(":read"):
            assert permission not in write_permissions, f"{scope} maps onto {permission}"


def test_all_scopes_matches_the_enum() -> None:
    """ALL_SCOPES is what the create endpoint's OpenAPI description lists;
    a hand-maintained copy would be the first thing to go stale."""
    assert set(ALL_SCOPES) == {scope.value for scope in Scope}


def test_parse_scopes_names_the_offending_value() -> None:
    with pytest.raises(ValueError, match="orgs:destroy"):
        parse_scopes(["orgs:read", "orgs:destroy"])


def test_parse_scopes_returns_members_of_the_enum() -> None:
    assert parse_scopes(["orgs:read"]) == [Scope.ORGS_READ]
