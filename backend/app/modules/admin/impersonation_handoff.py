"""
T-261: the Redis-side state behind handing a live impersonation session to a
superadmin's own phone.

Two short-lived records live here, both keyed by a hash rather than by the
secret itself, and neither of them a cache (so neither belongs in
app.core.cache_keys -- same reasoning as app.core.token_blocklist, which this
module sits next to conceptually):

- **the handoff record** -- an exchange token mapping to the impersonation
  JWT that was already minted by T-142. The token is what travels through a
  QR code and an OS-level deep link, i.e. across two surfaces neither the
  browser nor the app controls, so it is single-use, expires in two minutes,
  and is stored under `sha256(token)` -- a Redis dump (or an operator with
  `KEYS`/`SCAN`) reveals no usable token, the same posture as the
  refresh-token table's `token_hash` column.

- **the session record** -- the `reason` a superadmin typed when they started
  impersonating, keyed by the access token's `jti`. The confirmation screen on
  the phone has to show *why* this session exists before the operator accepts
  it, and the reason is not a JWT claim (it would be readable by anyone
  holding the token, including the impersonated user's own devices if the
  token ever leaked). It expires with the session it describes.

Both records carry an absolute `expires_at` that is checked in Python, on top
of the Redis TTL that would eventually evict them. The TTL is the cleanup
mechanism; the timestamp is the *rule*, and it is what makes expiry testable
without waiting two real minutes for a key to vanish.
"""

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis

from app.models._mixins import utcnow
from app.modules.auth.utils import hash_token

_HANDOFF_PREFIX = "impersonation:handoff:"
_SESSION_PREFIX = "impersonation:session:"

# T-261's spec value. Short on purpose: the window only has to cover "read a
# QR code off a screen and tap Confirm", and the token is readable by anything
# that can see the admin's monitor for as long as it lives.
HANDOFF_TTL_SECONDS = 120


def generate_exchange_token() -> str:
    """A URL-safe secret with the same entropy as a refresh token (32 bytes).

    It ends up in a `forge://` query string, so it must survive URL encoding
    untouched -- `token_urlsafe` is exactly that alphabet.
    """
    return secrets.token_urlsafe(32)


def _handoff_key(exchange_token: str) -> str:
    return f"{_HANDOFF_PREFIX}{hash_token(exchange_token)}"


def _session_key(jti: UUID) -> str:
    return f"{_SESSION_PREFIX}{jti}"


@dataclass(frozen=True)
class HandoffRecord:
    """What the phone gets back once it proves it holds the exchange token."""

    access_token: str
    user_id: UUID
    user_email: str
    impersonator_id: UUID
    reason: str
    # When the *impersonation session* ends -- the JWT's own exp, not this
    # record's. The mobile banner counts down against it, so it has to survive
    # the exchange rather than being recomputed on a device whose clock the
    # server does not control.
    session_expires_at: datetime
    # When the *exchange token* stops being redeemable.
    expires_at: datetime

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "user_id": str(self.user_id),
                "user_email": self.user_email,
                "impersonator_id": str(self.impersonator_id),
                "reason": self.reason,
                "session_expires_at": self.session_expires_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
            }
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> "HandoffRecord":
        data = json.loads(raw)
        return cls(
            access_token=data["access_token"],
            user_id=UUID(data["user_id"]),
            user_email=data["user_email"],
            impersonator_id=UUID(data["impersonator_id"]),
            reason=data["reason"],
            session_expires_at=datetime.fromisoformat(data["session_expires_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )


async def store_handoff(redis: Redis, exchange_token: str, record: HandoffRecord) -> None:
    await redis.set(_handoff_key(exchange_token), record.to_json(), ex=HANDOFF_TTL_SECONDS)


async def peek_handoff(redis: Redis, exchange_token: str) -> HandoffRecord | None:
    """Reads the record without consuming it -- the confirmation screen's
    preview. Returns None for an unknown, already-consumed, or expired token,
    and deletes an expired one on the way out so a clock-skewed TTL can't
    leave a record readable past its own deadline."""
    raw = await redis.get(_handoff_key(exchange_token))
    if raw is None:
        return None
    record = HandoffRecord.from_json(raw)
    if utcnow() >= record.expires_at:
        await redis.delete(_handoff_key(exchange_token))
        return None
    return record


async def take_handoff(redis: Redis, exchange_token: str) -> HandoffRecord | None:
    """Reads *and* consumes the record: the single-use guarantee.

    GETDEL rather than GET-then-DELETE so two devices racing the same QR code
    cannot both come away with a session -- exactly one of them gets the
    record, the loser gets None and a 401, with no window in between.
    """
    raw = await redis.getdel(_handoff_key(exchange_token))
    if raw is None:
        return None
    record = HandoffRecord.from_json(raw)
    return None if utcnow() >= record.expires_at else record


async def store_session_reason(redis: Redis, jti: UUID, reason: str, ttl_seconds: int) -> None:
    if ttl_seconds > 0:
        await redis.set(_session_key(jti), reason, ex=ttl_seconds)


async def get_session_reason(redis: Redis, jti: UUID) -> str | None:
    raw: bytes | str | None = await redis.get(_session_key(jti))
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


def handoff_expiry() -> datetime:
    return utcnow() + timedelta(seconds=HANDOFF_TTL_SECONDS)
