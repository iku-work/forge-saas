"""
T-314: the published public contract, `/api/v1/openapi-public.json`.

The property worth defending is a negative one -- **an internal endpoint must
never appear in it**. `/openapi.json` describes everything this deployment
serves and is dev-only for that reason; the document here is what an
integrator builds against, and anything that leaks into it becomes something
somebody depends on.

It is derived from `require_scope` rather than from a list, so the set is
asserted here the same way `test_api_key_auth.py` asserts the surface: by
naming it exactly. Widening it is a one-line diff in both places, which is
the review conversation that ought to happen.

`public_api` ships off, so the route is not on the app every other test file
uses -- the document is built directly from `app.main`'s routes instead,
which is the same set `build_public_schema` would see behind the endpoint.
"""

import json

import pytest
from fastapi.routing import APIRoute

from app.api.v1.public_openapi import (
    PUBLIC_API_VERSION,
    build_public_schema,
    public_route_contexts,
)
from app.main import app
from app.modules.api_keys.scopes import ALL_SCOPES


@pytest.fixture(scope="module")
def schema() -> dict:  # type: ignore[type-arg]
    return build_public_schema(list(app.routes))


def test_the_public_document_contains_exactly_the_scoped_routes(schema: dict) -> None:  # type: ignore[type-arg]
    assert set(schema["paths"]) == {
        "/api/v1/orgs/{org_id}",
        "/api/v1/orgs/{org_id}/members",
    }


def test_no_management_or_admin_route_leaks_into_it(schema: dict) -> None:  # type: ignore[type-arg]
    """The failure this exists to catch: a document that quietly grows.

    Checked as a property of every path rather than as a list of forbidden
    ones, so a new internal prefix is caught without anyone remembering to
    add it here.
    """
    rendered = json.dumps(schema)
    for forbidden in ("/api/v1/admin", "/api/v1/api-keys", "/api/v1/auth", "/api/v1/webhooks"):
        assert forbidden not in rendered, (
            f"{forbidden} appears in the published contract. If it is a securitySchemes "
            f"tokenUrl, _replace_security_scheme has stopped covering a scheme."
        )


def test_the_security_scheme_describes_a_key_rather_than_a_password_login(
    schema: dict,  # type: ignore[type-arg]
) -> None:
    """FastAPI documents `oauth2_scheme` as a password flow against
    `/api/v1/auth/login`, which an integrator holding an API key cannot use
    and which is not in this document."""
    schemes = schema["components"]["securitySchemes"]
    assert schemes
    for scheme in schemes.values():
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"
        assert scheme["bearerFormat"].startswith("fk_live_")


def test_every_operation_names_the_scope_a_key_needs(schema: dict) -> None:  # type: ignore[type-arg]
    """Otherwise an integrator discovers the scope by trying one and reading
    the 403, which is a support ticket with extra steps."""
    for operations in schema["paths"].values():
        for operation in operations.values():
            scope = operation.get("x-forge-scope")
            assert scope in ALL_SCOPES, operation


def test_the_document_documents_authentication_scopes_rate_limits_and_versioning(
    schema: dict,  # type: ignore[type-arg]
) -> None:
    """T-314's "Done when": a newcomer can create a key and make a call using
    only the published page. Each of these is a thing they cannot guess."""
    description = schema["info"]["description"]
    assert "Authorization: Bearer" in description
    assert "X-Org-ID" in description
    assert "X-RateLimit-Limit" in description
    assert "Retry-After" in description
    for scope in ALL_SCOPES:
        assert scope in description
    assert "breaking change" in description.lower()
    assert "90 days" in description


def test_the_contract_version_is_not_the_deployments_version(schema: dict) -> None:  # type: ignore[type-arg]
    """`app.version` moves with every deploy. A contract version that did the
    same would make "we did not break anything" unsayable."""
    assert schema["info"]["version"] == PUBLIC_API_VERSION
    assert schema["info"]["version"] != app.version


def test_public_routes_are_real_routes_on_this_app() -> None:
    """And that they carry their *mounted* path.

    `build_router()` includes every sub-router lazily, so an APIRoute reached
    through one reports `/orgs/{org_id}` rather than `/api/v1/orgs/{org_id}`
    -- a document built from those paths would publish endpoints nobody can
    call.
    """
    contexts = public_route_contexts(list(app.routes))
    assert contexts
    assert all(isinstance(context.route, APIRoute) for context in contexts)
    assert all(context.path.startswith("/api/v1/") for context in contexts)
