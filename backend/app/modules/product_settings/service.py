"""
ProductSettingsService (T-263): read and write the global runtime switches.

Two callers with very different needs share this file, which is why the read
path is a module-level function rather than a method:

  * **Routers** (`/config`, `/admin/settings/product`) hold a request-scoped
    `AsyncSession` from FastAPI's dependency graph and can build the service
    normally.
  * **`MaintenanceModeMiddleware`** runs *before* any dependency is solved --
    Starlette middleware has no access to the DI graph at all -- and still has
    to know whether maintenance is on. `read_settings()` therefore takes a
    Redis handle and opens its own short-lived session only when Redis misses,
    via `settings_session_scope`.

The caching contract is the one T-240 established for every other hot read:
Redis with a 60s TTL, read-through, and the *write* path busts the key before
it returns (so an operator flipping maintenance mode sees it take effect on
the next request, not up to a minute later). A Redis that is down degrades to
a DB read per call rather than failing -- see app.core.cache's docstring; the
cost of that during a Redis outage is one indexed primary-key lookup per
request, which is called out in docs/runbooks/maintenance-mode.md.

Authorization is *not* enforced here. Every route that reaches `set`/
`replace_all` sits behind `require_superadmin` (app.modules.admin.router), and
a service that re-checked it would need a `User` it has no other use for --
same division of labour as AdminBillingOverrideService.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from app.core import cache, cache_keys, db
from app.modules.audit.service import AuditService

from . import keys
from .models import SINGLETON_ID
from .repository import ProductSettingsRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

# Same 60s as every other read-through in this codebase (app.core.cache's
# DEFAULT_TTL_SECONDS). Named here because T-263 states it as a requirement of
# *this* switch specifically -- an operator has to be able to reason about the
# worst-case delay between flipping maintenance mode and the last stale worker
# noticing, and that number is this one.
TTL_SECONDS = 60

RESOURCE_TYPE = "product_settings"
AUDIT_ACTION = "product_settings.changed"


@asynccontextmanager
async def settings_session_scope() -> AsyncIterator[AsyncSession]:
    """A short-lived primary session for callers outside FastAPI's DI graph.

    The maintenance middleware is the only such caller. Looked up on the
    module at call time (like everything in app.core.db), which is also the
    seam tests/conftest.py's `client` fixture patches so a middleware read
    lands on the test transaction rather than opening a second, uncommitted
    connection to a different database.

    Reads only. Nothing here commits, and the row it returns is detached the
    moment the scope closes.
    """
    session = db.session_factory_for(read_only=False)()
    try:
        yield session
    finally:
        await session.close()


async def read_settings(redis: Redis, session: AsyncSession | None = None) -> dict[str, Any]:
    """Every stored setting, read through Redis.

    `session` is the caller's own request-scoped session, and passing it
    matters: on a cache miss this reads one row, and a caller that already
    holds a session should not open a *second* connection to do it. Only the
    maintenance middleware omits it, because Starlette runs it before FastAPI
    solves a single dependency and there is no session to pass -- that is what
    `settings_session_scope` is for.

    Returns `{}` when the singleton row is missing entirely -- a database
    restored from before T-263's migration, or a test that truncated the
    table. Every consumer reads a missing key as "at its default", so an
    absent row and an empty one behave identically, and neither can take the
    API down.
    """

    async def _load() -> cache.JsonDict | None:
        if session is not None:
            row = await ProductSettingsRepository(session).get()
        else:
            async with settings_session_scope() as own_session:
                row = await ProductSettingsRepository(own_session).get()
        # `{}` rather than None on a missing row: app.core.cache.cached_json
        # deliberately never caches a None (negative caching would hide a
        # just-created row for a full TTL), and "no settings row" is a
        # permanent state, not a race -- caching it is what keeps a fresh
        # deployment from doing a DB read on every single request.
        return dict(row.settings) if row is not None else {}

    payload = await cache.cached_json(
        redis, cache_keys.product_settings_key(), _load, ttl=TTL_SECONDS
    )
    return payload or {}


def maintenance_message(settings: dict[str, Any], fallback: str) -> str:
    """The operator's maintenance copy, or `fallback` when they left it blank.

    Rendered verbatim -- it is operator-written prose, not an i18n key, so it
    is never passed through `t()`. `fallback` is what the caller has already
    translated for the user's own locale.
    """
    message = settings.get(keys.MAINTENANCE_MESSAGE)
    if isinstance(message, str) and message.strip():
        return message
    return fallback


def is_maintenance_mode(settings: dict[str, Any]) -> bool:
    return settings.get(keys.MAINTENANCE_MODE) is True


def is_signup_disabled(settings: dict[str, Any]) -> bool:
    return settings.get(keys.SIGNUP_DISABLED) is True


def global_banner(settings: dict[str, Any]) -> dict[str, Any] | None:
    """The banner as `/config` should report it, or None.

    Re-validated on the way *out*, not just on the way in: this row is the one
    place in the system an operator can put arbitrary JSON, and a value that
    predates a change to the banner shape (or that arrived through a direct
    `UPDATE` during an incident) must not become a 500 on the public,
    unauthenticated config endpoint.
    """
    raw = settings.get(keys.GLOBAL_BANNER)
    if raw is None:
        return None
    try:
        return cast("dict[str, Any]", keys.validate(keys.GLOBAL_BANNER, raw))
    except keys.SettingsValidationError:
        return None


def kill_switch(settings: dict[str, Any]) -> dict[str, Any]:
    raw = settings.get(keys.EMERGENCY_KILL_SWITCH)
    return raw if isinstance(raw, dict) else {}


class ProductSettingsService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        self._repo = ProductSettingsRepository(session)
        self._audit = AuditService(session)

    async def all(self) -> dict[str, Any]:
        """Every stored setting. Reads straight from the DB, not the cache:
        the only caller is the admin UI, which must never show an operator a
        value up to a minute out of date while they decide whether to change
        it."""
        row = await self._repo.get()
        return dict(row.settings) if row is not None else {}

    async def get(self, key: str) -> Any:  # noqa: ANN401 -- a setting is arbitrary JSON by design
        """One setting's value, or None if unset. Redis-cached (60s),
        invalidated on write."""
        return (await read_settings(self._redis, self._session)).get(key)

    async def set(self, key: str, value: Any, actor_id: UUID) -> dict[str, Any]:  # noqa: ANN401 -- see get
        """Set (or, with `value=None`, unset) one key.

        Returns the full settings dict as stored, so a caller that needs to
        render the new state doesn't have to re-read it.
        """
        return await self.apply({key: value}, actor_id)

    async def apply(self, changes: dict[str, Any], actor_id: UUID) -> dict[str, Any]:
        """Merge `changes` into the stored settings; a `None` value unsets its
        key. The whole merge is one audit event with one diff -- an operator
        who changes three switches in one form submission performed one
        action, and splitting it into three rows would make the trail harder
        to read, not easier."""
        current = await self.all()
        merged = dict(current)
        for key, value in changes.items():
            validated = keys.validate(key, value)
            if validated is None:
                merged.pop(key, None)
            else:
                merged[key] = validated
        return await self._store(current, merged, actor_id)

    async def replace_all(self, settings: dict[str, Any], actor_id: UUID) -> dict[str, Any]:
        """Wholesale replacement, for the admin UI's raw-JSON editor. Keys
        absent from `settings` are removed -- that is what "replace" means and
        what the editor shows, but it is also why the UI sends the document it
        loaded rather than a fragment."""
        current = await self.all()
        validated = {
            key: value
            for key, value in ((k, keys.validate(k, v)) for k, v in settings.items())
            if value is not None
        }
        return await self._store(current, validated, actor_id)

    async def _store(
        self, before: dict[str, Any], after: dict[str, Any], actor_id: UUID
    ) -> dict[str, Any]:
        if before == after:
            # No row touched, no audit event, no cache bust: an operator
            # re-submitting an unchanged form is not a change, and an audit
            # trail that records it is one an incident reviewer has to filter.
            return after
        await self._repo.replace(after, actor_id)
        await self._audit.log(
            action=AUDIT_ACTION,
            resource_type=RESOURCE_TYPE,
            resource_id=str(SINGLETON_ID),
            # Global, not org-scoped -- there is no org this event belongs to.
            org_id=None,
            actor_id=actor_id,
            diff=_diff(before, after),
            # T-141's "everything elevated" severity: flipping maintenance
            # mode or a kill switch is a platform-wide action, and an
            # incident review should find it without parsing action strings.
            severity="elevated",
        )
        # First of two busts. This one runs inside the caller's still-open
        # transaction, so on its own it leaves a narrow race: a concurrent
        # request that misses the cache between this DELETE and the COMMIT
        # loads the *pre-commit* row and re-caches the old answer for a full
        # TTL -- which an operator experiences as "I turned maintenance mode on
        # and nothing happened", and reacts to by flipping it again.
        # `invalidate_cache()` below is what actually closes that window, and
        # the router calls it after committing. This one still earns its place:
        # it covers a caller with no separate commit step, and a redundant
        # DELETE costs nothing.
        await self.invalidate_cache()
        return after

    async def invalidate_cache(self) -> None:
        """Drop the cached settings copy. Called by `_store` and again by the
        router *after* its commit -- see the comment in `_store` for why once
        is not enough."""
        await cache.invalidate(self._redis, cache_keys.product_settings_key())


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Only the keys that actually changed, each as `{"from": ..., "to": ...}`.

    A whole-document before/after would be an audit row that grows with the
    settings blob and that a reviewer has to eyeball for the one line that
    moved.
    """
    changed: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            changed[key] = {"from": old, "to": new}
    return changed
