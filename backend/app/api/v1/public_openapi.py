"""
The **public** API contract (T-314): `/api/v1/openapi-public.json`.

A "public API" with no defined surface is half a feature, and the half that
is missing is the one integrators need. `/openapi.json` is not it: that
document describes every route this deployment serves, including the
management and admin surfaces, and it is served only in dev (§ASVS V14.3.2).
Publishing it would turn every internal endpoint into something somebody
built against.

So this endpoint emits a *second* document containing exactly the routes an
API key can call, and it derives that set rather than maintaining it: a route
is public if and only if its dependency tree declares a `require_scope`,
which `app.core.principal.declared_scope` reports the same way T-260's sweep
reports `blocked_action_kind`. Opening a route to keys and publishing it are
therefore one action, and there is no list to forget.

Each operation is stamped with `x-forge-scope`, the scope a key needs, so the
rendered documentation can say what to ask for instead of leaving a reader to
try one and read the 403.

**Routes are enumerated through `iter_route_contexts`, not `app.routes`.**
`build_router()` includes every sub-router lazily (T-061), so `app.routes`
holds a handful of wrappers rather than a hundred `APIRoute`s -- and an
`APIRoute` reached through one carries its path *without* the `/api/v1`
prefix. The context is what knows the mounted path, which is why both the
filter and `get_openapi` are handed contexts. `tests/modules/
test_api_key_auth.py`'s surface sweep walks the same structure.

Registered only when `public_api` is enabled -- with the module off a key
cannot authenticate at all (see app.core.principal), and a contract nobody
can call is worse than no contract. It is unauthenticated, like
`/api/v1/billing/plans`: a published contract that requires a credential to
read cannot be read by the person deciding whether to integrate.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.dependencies.models import Dependant
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from starlette.responses import JSONResponse

from app.core.principal import declared_scope
from app.modules.api_keys.models import KEY_PREFIX
from app.modules.api_keys.scopes import ALL_SCOPES

router = APIRouter(tags=["public_api"])

PUBLIC_OPENAPI_PATH = "/openapi-public.json"

_TITLE = "ForgeSaaS Public API"
# Separate from the app's own version on purpose. `app.version` moves with
# every deploy; this one moves only when the *contract* does, which is what
# the deprecation policy below is written against.
PUBLIC_API_VERSION = "1.0.0"

_DESCRIPTION = f"""
Every endpoint an API key can call, and nothing else.

## Authenticating

Send the key as a bearer token:

    Authorization: Bearer {KEY_PREFIX}<prefix>_<secret>
    X-Org-ID: <the organization's id>

`X-Org-ID` is required: a key belongs to one organization, and naming the
wrong one is a 403 rather than an empty result, so a misconfigured
integration fails loudly.

## Scopes

A key carries an explicit list of scopes and can reach only the endpoints
whose `x-forge-scope` it holds. The vocabulary is closed: {", ".join(ALL_SCOPES)}.
Asking for a scope outside it is a 422 naming the offending value.

## Rate limits

Every response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
`X-RateLimit-Reset`, and a 429 additionally carries `Retry-After`. The limit
is per key, so one integration cannot spend another's budget.

## Errors

Every response, success or failure, is the same envelope:
`{{"data": ..., "error": ..., "meta": {{"request_id": ...}}}}`. Quote
`meta.request_id` in a support request -- it is what identifies the exact
call in the logs.

## Versioning

A breaking change gets a new path prefix; this one is never redefined
underneath you. Breaking means: removing an endpoint or a response field,
making an optional request field required, narrowing an accepted value set,
or changing a field's type. Adding an endpoint, adding a response field and
adding an optional request field are **not** breaking, so build clients that
ignore unknown fields. Any breaking change is announced at least 90 days
ahead, and the superseded version keeps working for those 90 days.
"""


def _scopes_in(dependant: Dependant | None) -> list[str]:
    """Every scope declared anywhere in a route's dependency tree.

    Recursive, because `require_scope` is reached as a sub-dependency of the
    endpoint's own parameter rather than sitting at the top level.
    """
    if dependant is None:
        return []
    found: list[str] = []
    for dependency in dependant.dependencies:
        scope = declared_scope(dependency.call)
        if scope is not None:
            found.append(scope)
        found.extend(_scopes_in(dependency))
    return found


def _scope_for(context: RouteContext) -> str | None:
    route = context.route
    if not isinstance(route, APIRoute):
        return None
    scopes = _scopes_in(route.dependant)
    return scopes[0] if scopes else None


def public_route_contexts(routes: list[Any]) -> list[RouteContext]:
    """Every route whose dependency tree declares a `require_scope`.

    Derived, not listed --
    `test_the_public_surface_is_exactly_the_routes_that_declare_a_scope`
    already pins the resulting set, which is the review conversation
    widening it is supposed to trigger.
    """
    return [context for context in iter_route_contexts(routes) if _scope_for(context) is not None]


def build_public_schema(routes: list[Any]) -> dict[str, Any]:
    contexts = public_route_contexts(routes)
    schema: dict[str, Any] = get_openapi(
        title=_TITLE,
        version=PUBLIC_API_VERSION,
        description=_DESCRIPTION,
        routes=list(contexts),
    )
    scope_by_operation: dict[tuple[str, str], str] = {}
    for context in contexts:
        scope = _scope_for(context)
        if scope is None:  # pragma: no cover -- the filter above guarantees one
            continue
        # `path` is Optional on the base class; every context reaching here
        # came from an APIRoute, which always has one.
        path = context.path or ""
        for method in context.methods or set():
            scope_by_operation[(path, method.lower())] = scope

    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            scope = scope_by_operation.get((path, method))
            if scope is not None:
                operation["x-forge-scope"] = scope
    _replace_security_scheme(schema)
    return schema


def _replace_security_scheme(schema: dict[str, Any]) -> None:
    """Describe the credential this document is actually about.

    Every route here is gated by `oauth2_scheme`, so FastAPI documents an
    OAuth2 password flow whose `tokenUrl` is `/api/v1/auth/login` -- true of a
    *session*, and actively misleading here: an integrator holding an API key
    has no password to post there, and the endpoint is not in this document.
    Replacing it with an HTTP bearer scheme is what makes the generated
    "Authorize" box in any renderer ask for the right thing.
    """
    components = schema.setdefault("components", {})
    existing = components.get("securitySchemes", {})
    components["securitySchemes"] = {
        name: {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": f"{KEY_PREFIX}<prefix>_<secret>",
            "description": (
                "An API key created in the app under Settings -> API keys. "
                "Send `X-Org-ID` alongside it."
            ),
        }
        for name in existing
    }


@router.get(PUBLIC_OPENAPI_PATH, include_in_schema=False)
async def public_openapi(request: Request) -> JSONResponse:
    """The public contract, as OpenAPI 3.1.

    Built per request rather than cached: it is a handful of routes, it is
    fetched by a build step and a documentation page rather than by traffic,
    and a cached copy is one more thing that can be stale after a deploy.
    """
    return JSONResponse(build_public_schema(list(request.app.routes)))
