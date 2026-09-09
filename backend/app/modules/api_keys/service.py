"""
Business logic for the api_keys module: minting, revoking and -- the part
that matters -- authenticating a key (T-310, T-311).

The layer that owns decisions. It talks to the database through the two
repositories, and knows nothing about HTTP: failures leave as typed
`DomainError`s and app.core.handlers turns each into a status code and an
error envelope. Nothing here commits; the router owns the transaction
boundary.

**Nothing here ever logs, returns or raises with the secret in it.** The full
key exists as a local variable inside `create_key` and in exactly one
response schema (`ApiKeyCreated`), and that is the whole of its lifetime on
this side of the wire. `authenticate` takes the presented key and returns a
row; its log lines carry the prefix, which is public by construction.
"""

import hmac
import secrets
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache, cache_keys
from app.core.exceptions import AuthenticationError, PlanLimitError, UnprocessableEntityError
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.webhooks import service as webhooks
from app.modules.webhooks.events import ApiKeyPayload, EventType

from .models import KEY_PREFIX, PREFIX_LENGTH, ApiKey
from .repository import ApiKeyLookupRepository, ApiKeyRepository
from .schemas import ApiKeyCreateRequest, ApiKeyUpdateRequest
from .scopes import Scope, parse_scopes

RESOURCE_TYPE = "api_key"

# T-313 caps the management list at this, and says so in a comment there
# rather than reaching for VirtualList. Enforced here as well as rendered
# there: a cap only the UI knows about is not a cap.
MAX_KEYS_PER_ORG = 50

# 32 bytes of CSPRNG output, per §7 and T-310. token_urlsafe's argument is
# *bytes of entropy*, not characters -- the string it returns is longer.
SECRET_BYTES = 32

# How often `last_used_at` may be written for one key. A write per request on
# a hot key is an N+1 wearing a different hat (T-243's property, applied to a
# column nobody pages).
LAST_USED_THROTTLE = timedelta(minutes=1)

# The Redis key that implements that throttle. Deliberately *not* in
# app.core.cache_keys: it is a lock, not a cached copy of a row, and it has no
# invalidation matrix to keep honest -- the same reason the token blocklist
# and the scheduler's locks live outside that module (see its docstring).
_TOUCH_LOCK_NAMESPACE = "api_key_touch"


def _touch_lock_key(key_id: UUID) -> str:
    return f"{_TOUCH_LOCK_NAMESPACE}:{key_id}"


def hash_secret(secret: str) -> str:
    """SHA-256, and the reason T-310 asks to be written down is in models.py's
    docstring: this is a 256-bit random secret, not a human-chosen password,
    so there is no dictionary for a work factor to slow down -- and argon2
    here would put ~100 ms of CPU on the hot path of every API request, which
    is the exact failure T-223's load test caught."""
    return sha256(secret.encode()).hexdigest()


def split_presented_key(presented: str) -> tuple[str, str] | None:
    """`fk_live_<prefix>_<secret>` -> (prefix, secret), or None if the string
    is not shaped like a key at all.

    Returning None rather than raising keeps the caller's control flow
    honest: a malformed key and an unknown key must be indistinguishable to
    the client (both are 401), and making the parse failure an exception
    invites a different error message for one of them.
    """
    if not presented.startswith(KEY_PREFIX):
        return None
    body = presented[len(KEY_PREFIX) :]
    prefix, separator, secret = body.partition("_")
    if separator == "" or len(prefix) != PREFIX_LENGTH or secret == "":
        return None
    return prefix, secret


def looks_like_api_key(token: str) -> bool:
    """The dispatch T-311 turns on: one `Authorization: Bearer` header
    carries both kinds of credential, and which one this is is decided by the
    prefix alone. A token that does not start with it goes down the JWT path
    completely untouched."""
    return token.startswith(KEY_PREFIX)


class ApiKeyService:
    """Management operations. Org-scoped, like the repository behind it."""

    def __init__(self, session: AsyncSession, org_id: UUID, redis: Redis | None = None) -> None:
        self._session = session
        self._org_id = org_id
        self._redis = redis
        self._repo = ApiKeyRepository(session, org_id)
        self._audit = AuditService(session)

    async def create_key(self, user_id: UUID, payload: ApiKeyCreateRequest) -> tuple[ApiKey, str]:
        """Mints a key and returns it with its one-and-only presentation.

        The returned string is the caller's single opportunity to read it. It
        is not stored, so this is not a convenience -- it is the contract.
        """
        if await self._repo.count_live() >= MAX_KEYS_PER_ORG:
            raise PlanLimitError(
                "errors.api_key_limit_reached",
                fallback=f"This organization is limited to {MAX_KEYS_PER_ORG} API keys",
                params={"limit": str(MAX_KEYS_PER_ORG)},
            )
        scopes = self._parse_scopes(payload.scopes)
        if payload.expires_at is not None and payload.expires_at <= utcnow():
            raise UnprocessableEntityError(
                "errors.api_key_expiry_in_the_past",
                fallback="expires_at must be in the future",
            )

        # token_hex, not token_urlsafe -- see PREFIX_LENGTH's comment in
        # models.py for the bug that distinction fixes. //2 because one byte
        # renders as two hex characters.
        prefix = secrets.token_hex(PREFIX_LENGTH // 2)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        key = await self._repo.create(
            name=payload.name,
            prefix=prefix,
            key_hash=hash_secret(secret),
            scopes=[scope.value for scope in scopes],
            created_by=user_id,
            expires_at=payload.expires_at,
        )
        # The audit trail records that a key was minted, its id and its
        # scopes -- never the key itself. An audit feed is read by more people
        # than a database is.
        await self._log(
            "api_key.created",
            user_id,
            key.id,
            diff={"name": key.name, "prefix": key.prefix, "scopes": key.scopes},
        )
        # A prefix that has never been looked up cannot have a stale entry --
        # but negative results are never cached either, so the only thing this
        # costs is one Redis DEL per key created, and it removes the question
        # entirely rather than leaving it to be reasoned about.
        await self._invalidate(prefix)
        # T-320. A security integration wants to know a credential was minted,
        # by whom and with what reach -- and nothing about the credential
        # itself, which is why ApiKeyPayload has no field that could carry it.
        await webhooks.emit(
            self._session,
            self._org_id,
            EventType.API_KEY_CREATED,
            ApiKeyPayload(api_key_id=key.id, name=key.name, prefix=key.prefix, scopes=key.scopes),
        )
        return key, f"{KEY_PREFIX}{prefix}_{secret}"

    async def list_keys(self, after: UUID | None, limit: int) -> tuple[list[ApiKey], UUID | None]:
        return await self._repo.list_cursor(after, limit)

    async def get_key(self, key_id: UUID) -> ApiKey:
        return await self._repo.get_or_raise(key_id)

    async def update_key(self, user_id: UUID, key_id: UUID, payload: ApiKeyUpdateRequest) -> ApiKey:
        changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            raise UnprocessableEntityError(
                "errors.no_fields_to_update", fallback="No fields to update"
            )
        key = await self._repo.update(key_id, **changes)
        await self._log("api_key.updated", user_id, key_id, diff=changes)
        # T-312's override is read off the cached row, so a change to it has
        # to bust the entry or the old budget survives for the rest of the TTL.
        await self._invalidate(key.prefix)
        return key

    async def revoke_key(self, user_id: UUID, key_id: UUID) -> ApiKey:
        """Kills the key without hiding it.

        `revoked_at`, not `deleted_at`: the row stays in the list, because
        "this key was revoked on the 3rd, by whom, and what could it reach"
        is the question somebody asks during an incident, and a row that
        vanished cannot answer it.
        """
        key = await self._repo.get_or_raise(key_id)
        if key.revoked_at is None:
            key = await self._repo.update(key_id, revoked_at=utcnow())
            await self._log("api_key.revoked", user_id, key_id, diff={"prefix": key.prefix})
            await webhooks.emit(
                self._session,
                self._org_id,
                EventType.API_KEY_REVOKED,
                ApiKeyPayload(
                    api_key_id=key.id, name=key.name, prefix=key.prefix, scopes=key.scopes
                ),
            )
        # Unconditional, and outside the branch above: revocation has to take
        # effect on the *next* request rather than at the end of the cache
        # TTL, and re-revoking an already-revoked key is exactly what somebody
        # does when they are trying to make sure.
        await self._invalidate(key.prefix)
        return key

    async def delete_key(self, user_id: UUID, key_id: UUID) -> None:
        key = await self._repo.get_or_raise(key_id)
        await self._repo.soft_delete(key_id)
        await self._log("api_key.deleted", user_id, key_id, diff={"prefix": key.prefix})
        await self._invalidate(key.prefix)

    def _parse_scopes(self, values: list[str]) -> list[Scope]:
        try:
            return parse_scopes(values)
        except ValueError as exc:
            unknown = str(exc)
            raise UnprocessableEntityError(
                "errors.unknown_scope",
                fallback=f"Unknown scope: {unknown}",
                params={"scope": unknown},
            ) from exc

    async def _invalidate(self, prefix: str) -> None:
        if self._redis is not None:
            await cache.invalidate(self._redis, cache_keys.api_key_lookup_key(prefix))

    async def _log(
        self, action: str, user_id: UUID | None, key_id: UUID, diff: dict[str, Any] | None = None
    ) -> None:
        await self._audit.log(
            action=action,
            resource_type=RESOURCE_TYPE,
            resource_id=str(key_id),
            org_id=self._org_id,
            actor_id=user_id,
            diff=diff,
        )


class ApiKeyAuthenticator:
    """The authentication path (T-311), and the reason it is not a method on
    the service above: it has no org until it has resolved one, so it cannot
    be constructed the way an org-scoped service is."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        self._repo = ApiKeyLookupRepository(session)

    async def authenticate(self, presented: str) -> ApiKey:
        """The presented key's row, or `AuthenticationError`.

        Every failure mode -- malformed, unknown prefix, wrong secret,
        expired, revoked -- raises the *same* error with the same message.
        A client learns that the credential did not work and nothing else;
        telling it "that prefix exists but the secret is wrong" would turn
        the prefix, which is not a secret, into an oracle for which of them
        are real.
        """
        parsed = split_presented_key(presented)
        if parsed is None:
            raise self._rejected()
        prefix, secret = parsed

        key = await self._load(prefix)
        if key is None:
            raise self._rejected()
        # compare_digest, not ==: the digest is derived from a secret, and a
        # timing oracle on it is free to avoid.
        if not hmac.compare_digest(key.key_hash, hash_secret(secret)):
            raise self._rejected()
        if key.revoked_at is not None:
            raise self._rejected()
        if is_expired(key):
            raise self._rejected()
        return key

    async def touch_used(self, key: ApiKey) -> bool:
        """Stamp `last_used_at`, at most once per key per minute.

        The throttle is a Redis `SET NX EX`, which is atomic across every
        worker in the deployment -- a per-process timestamp would let N
        replicas write N times a minute. Returns whether a write happened, so
        the caller knows whether it has anything to commit.

        Redis being unreachable means *no* write, not a write on every
        request: the fallback for a broken throttle has to be the safe side
        of it. A `last_used_at` that stops advancing during a Redis outage is
        a cosmetic loss; a write per request is a load problem on top of an
        outage.
        """
        try:
            acquired = await self._redis.set(
                _touch_lock_key(key.id),
                "1",
                nx=True,
                ex=int(LAST_USED_THROTTLE.total_seconds()),
            )
        except RedisError as exc:
            logger.warning(
                "api_key.touch_throttle_unavailable: prefix={} error={}", key.prefix, exc
            )
            return False
        if not acquired:
            return False
        await self._repo.touch_used(key.id, utcnow())
        return True

    async def _load(self, prefix: str) -> ApiKey | None:
        """The cached row for a prefix (T-240).

        A miss loads from the database and caches the row for the standard
        TTL; a prefix that resolves to nothing is *not* cached, because
        negative caching here would let a key created a second ago fail for
        the rest of the minute -- and cache.cached_json already declines to
        cache a None loader result for exactly that reason.
        """

        async def _loader() -> cache.JsonDict | None:
            row = await self._repo.get_by_prefix(prefix)
            return cache.dump(row) if row is not None else None

        payload = await cache.cached_json(
            self._redis, cache_keys.api_key_lookup_key(prefix), _loader
        )
        return cache.hydrate(ApiKey, payload) if payload is not None else None

    @staticmethod
    def _rejected() -> AuthenticationError:
        return AuthenticationError("errors.invalid_api_key", fallback="Invalid or expired API key")


def is_expired(key: ApiKey, now: datetime | None = None) -> bool:
    return key.expires_at is not None and key.expires_at <= (now or utcnow())
