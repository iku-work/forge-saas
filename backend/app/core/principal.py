"""
A second kind of principal (T-311): who is acting, when "who" might not be a
person.

Every authenticated route in this codebase until now resolved a `User` and an
org-scoped `Membership`. A third party holding an API key has neither -- it
has an organization, a set of scopes, and an identity that is the key itself.
`CurrentPrincipal` is the one shape both kinds collapse to at the point a
route actually needs to know "which org, and may it do this".

Three decisions are worth stating, because each is the one that keeps this
from becoming a hole:

**Opt-in, per endpoint, always.** A route accepts an API key only by
declaring `require_scope(...)`. The default for every existing and every
future endpoint is that keys cannot call it, and there is deliberately no
blanket swap of `get_current_user` for something key-aware -- that swap is
how "we opened one read endpoint" becomes "the whole internal API is a public
API" three tasks later. `tests/modules/test_api_key_auth.py` asserts the
negative case directly: a valid key on a route without `require_scope` is
rejected.

**`get_current_user` is untouched.** A JWT authenticates exactly as it did,
on the same header, through the same dependency, on every route that has not
opted in. The dispatch below is on the token's *prefix*, so a token that does
not start with `fk_live_` never reaches a line of api-key code.

**The module toggle is checked here, not only at the router.** With
`public_api` off, its routes are never registered -- but `api_key` rows may
still physically exist, because `MODULE_TABLES` exists precisely so that
disabling a module does not drop its data. A key minted while the module was
on must stop authenticating the moment it is turned off, which is a check on
the auth path rather than a fact about the routing table.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_redis, oauth2_scheme, resolve_user_from_token
from app.core.exceptions import (
    AuthenticationError,
    DomainError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
from app.core.module_loader import is_enabled
from app.modules.api_keys import rate_limit as api_key_rate_limit
from app.modules.api_keys.scopes import SCOPE_PERMISSIONS, Scope
from app.modules.api_keys.service import ApiKeyAuthenticator, looks_like_api_key
from app.modules.auth.permissions import ROLE_PERMISSIONS
from app.modules.tenancy.repository import MembershipRepository

_SCOPE_ATTR = "__public_api_scope__"

ActorType = Literal["user", "api_key"]


@dataclass(frozen=True)
class CurrentPrincipal:
    """Whoever is making this request, in the only terms a route needs.

    `actor_id` is a user id or a key id depending on `actor_type`, and the
    two are never conflated: an audit event written for a key-authenticated
    write records `actor_type="api_key"` and the key's id, never a user's --
    "the person who minted the key" is not the person who made this request,
    and pretending otherwise is how an audit trail starts lying.
    """

    actor_type: ActorType
    actor_id: UUID
    org_id: UUID
    scopes: frozenset[str]

    @property
    def is_api_key(self) -> bool:
        return self.actor_type == "api_key"

    def audit_fields(self) -> dict[str, str]:
        """What an audit event should carry about this principal. Never the
        key -- the id identifies the row, which is enough to answer "which
        credential did this" without the credential."""
        return {"actor_type": self.actor_type, "actor_id": str(self.actor_id)}


def _requested_org_id(request: Request) -> UUID | None:
    """The organization this request addresses, from the path if the route
    names one and from `X-Org-ID` otherwise.

    Read off `request.path_params` rather than declared as a parameter on the
    checker below, and that is not a shortcut: a dependency that declares
    `org_id: UUID` gets it bound from the path on the routes that have one
    and turned into a *query parameter* on the routes that do not -- the same
    FastAPI trap that shaped `get_session`'s signature in T-246. One
    dependency has to serve both route shapes, so it asks the request.
    """
    raw = request.path_params.get("org_id")
    if raw is not None:
        try:
            return UUID(str(raw))
        except ValueError as exc:
            # A dependency is solved *before* the route's own `org_id: UUID`
            # parameter is validated, so "the path converter will catch it in
            # a moment" is not true -- this runs first. Raising the same
            # status FastAPI would have is what keeps `/orgs/nonsense/members`
            # a 422 about the path rather than a 400 about a header the
            # caller did not send.
            raise UnprocessableEntityError(
                "errors.validation_failed", fallback="org_id is not a valid identifier"
            ) from exc
    header = request.headers.get("X-Org-ID")
    if header is None:
        return None
    try:
        return UUID(header)
    except ValueError as exc:
        raise DomainError(
            "errors.invalid_org_header", fallback="X-Org-ID is not a valid identifier"
        ) from exc


def require_scope(scope: Scope) -> Callable[..., Awaitable[CurrentPrincipal]]:
    """Gate a route on one scope, and open it to API keys by doing so.

    The dual-principal rule, in one place:

      * an **API key** passes when the scope is in its own `scopes` array and
        the organization it belongs to is the one the request addresses;
      * a **session** passes when its membership's role holds the Permission
        that `SCOPE_PERMISSIONS` maps the scope onto -- i.e. exactly the door
        it could already walk through. Opening a route to keys never widens
        what a *user* may do.

    A route that carries this is, by construction, enumerable: grep for
    `require_scope` and you have the public surface. T-314 turns that into a
    published document.
    """

    async def checker(
        request: Request,
        response: Response,
        token: Annotated[str | None, Depends(oauth2_scheme)],
        session: Annotated[AsyncSession, Depends(get_session)],
        redis: Annotated[Redis, Depends(get_redis)],
    ) -> CurrentPrincipal:
        if token is None:
            raise AuthenticationError(
                "errors.missing_bearer_token", fallback="Missing bearer token"
            )
        org_id = _requested_org_id(request)
        if org_id is None:
            raise DomainError("errors.org_header_required", fallback="X-Org-ID header is required")

        if looks_like_api_key(token):
            return await _principal_from_key(token, response, session, redis, scope, org_id)
        return await _principal_from_session(token, request, session, redis, scope, org_id)

    # setattr, not an assignment: the name is a private constant shared with
    # declared_scope() below, and a function object has no declared attribute
    # for mypy to accept the direct form.
    setattr(checker, _SCOPE_ATTR, scope.value)
    return checker


async def _principal_from_key(  # noqa: PLR0913 -- the checker's five inputs plus the scope it was built with
    token: str,
    response: Response,
    session: AsyncSession,
    redis: Redis,
    scope: Scope,
    org_id: UUID,
) -> CurrentPrincipal:
    if not is_enabled("public_api"):
        # See the module docstring: the routes are gone, the rows are not.
        raise AuthenticationError("errors.invalid_api_key", fallback="Invalid or expired API key")

    authenticator = ApiKeyAuthenticator(session, redis)
    key = await authenticator.authenticate(token)

    # §0.2, and the reason this is a 403 rather than a 404: the org is what
    # the *caller* named, not a row it discovered, so there is nothing to
    # conceal -- and a key holder pointed at the wrong org needs to be told
    # that rather than left to debug an empty result.
    if key.org_id != org_id:
        raise PermissionDeniedError(
            "errors.not_org_member", fallback="Not a member of this organization"
        )
    if scope.value not in key.scopes:
        raise PermissionDeniedError(
            "errors.missing_scope",
            fallback=f"This API key does not have the '{scope.value}' scope",
            params={"scope": scope.value},
        )

    # T-312, and after the scope check on purpose: a request that was never
    # going to be allowed should not spend the caller's budget. The headers
    # go on every response, not only the 429 -- a client that can see it is
    # at 3 of 600 can slow down before it is refused.
    verdict = api_key_rate_limit.check(key.id, key.rate_limit_per_minute)
    response.headers.update(verdict.headers())
    api_key_rate_limit.raise_for(verdict)

    # Throttled to one write per key per minute, and committed here because
    # a read endpoint's router has no commit of its own to piggyback on.
    if await authenticator.touch_used(key):
        await session.commit()

    return CurrentPrincipal(
        actor_type="api_key",
        actor_id=key.id,
        org_id=key.org_id,
        scopes=frozenset(key.scopes),
    )


async def _principal_from_session(  # noqa: PLR0913 -- as above
    token: str,
    request: Request,
    session: AsyncSession,
    redis: Redis,
    scope: Scope,
    org_id: UUID,
) -> CurrentPrincipal:
    user = await resolve_user_from_token(token, request, session, redis)
    membership = await MembershipRepository(session).get_by_user_and_org(user.id, org_id)
    if membership is None:
        raise PermissionDeniedError(
            "errors.not_org_member", fallback="Not a member of this organization"
        )
    permission = SCOPE_PERMISSIONS[scope]
    if permission not in ROLE_PERMISSIONS[membership.role]:
        raise PermissionDeniedError(
            "errors.missing_permission",
            fallback=f"Missing permission: {permission.value}",
            params={"permission": permission.value},
        )
    # A session's scopes are what its role could exercise, not a stored list.
    # Reported so a route can branch on capability without caring which kind
    # of principal it has.
    granted = {
        candidate.value
        for candidate, needed in SCOPE_PERMISSIONS.items()
        if needed in ROLE_PERMISSIONS[membership.role]
    }
    return CurrentPrincipal(
        actor_type="user",
        actor_id=user.id,
        org_id=org_id,
        scopes=frozenset(granted),
    )


def declared_scope(call: object) -> str | None:
    """The scope `call` gates on, or None if it is not a `require_scope` at
    all.

    The supported way to ask "is this dependency the public-API gate?" --
    tests/modules/test_api_key_auth.py walks every route's dependency tree
    through this to enumerate the public surface, so the marker attribute
    stays an implementation detail. Same shape as
    app.core.dependencies.blocked_action_kind.
    """
    value = getattr(call, _SCOPE_ATTR, None)
    return value if isinstance(value, str) else None
