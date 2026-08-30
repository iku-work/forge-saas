"""
The `api_key` table (T-310) -- an org-scoped, hashed, scoped, expiring
credential a third party presents instead of a session.

The presented key is `fk_live_<prefix>_<secret>` and the row stores two
halves of it:

  * `prefix` in clear, unique and indexed. It is what the lookup queries, and
    it is what the management UI shows so a person can tell two keys apart
    without ever seeing either one again.
  * `key_hash`, the SHA-256 digest of `secret`. The secret itself is 32 bytes
    from `secrets.token_urlsafe` and is returned exactly once, by the create
    endpoint. It is never stored, never logged and never in an error message
    (§7).

**SHA-256, not argon2, and the reason is the whole design.** Argon2 exists to
make a *dictionary* attack expensive against a secret a human chose. This
secret is 256 bits of CSPRNG output: there is no dictionary, and brute force
is already out of reach by many orders of magnitude, so the work factor buys
nothing. What it would cost is real -- roughly 100 ms of CPU on the hot path
of *every* API request, which is precisely the failure T-223's load test
caught when argon2 was blocking the event loop on login. The comparison is
`hmac.compare_digest` (app.modules.api_keys.service), not `==`, because the
digest is still a secret-derived value and a timing oracle on it is free to
avoid.

`key_hash` ends in `_hash`, so the Semgrep rule that demands `EncryptedString`
for secret-looking columns does not fire on it -- a digest is not a
recoverable secret and encrypting it would buy nothing. `RedactedReprMixin` is
declared all the same: a model that lands in a traceback or a Sentry event
should not print a credential digest next to the org it belongs to.

`created_by` is nullable, which is not an oversight either -- see T-036's
erasure path in app.workers.jobs.gdpr: erasing a user nulls this column
rather than deleting an organization's still-active credential.
"""

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.encrypted_type import RedactedReprMixin
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin

_TZ_DATETIME = DateTime(timezone=True)

# The visible half of every key this product mints. `fk` is the product's
# initials and `live` is the environment, which is the shape every API
# consumer already recognises from Stripe and GitHub -- and the reason it is
# worth copying is not familiarity but *scanning*: a fixed, searchable prefix
# is what makes a leaked credential findable by a secret scanner in a public
# repository. A product that renames it renames it here, once.
KEY_PREFIX = "fk_live_"

# How many characters of the random part are stored in clear as `prefix`.
# Long enough that a collision needs the birthday bound to be absurd (the
# column is UNIQUE, so a collision is a failed insert rather than a security
# problem), short enough to be readable in a table of keys.
#
# The prefix is **hex**, not `token_urlsafe` output, and that is not a style
# choice: urlsafe base64 includes `_`, `_` is the separator between the prefix
# and the secret, and a prefix that happened to contain one made
# `split_presented_key` cut the key in the wrong place -- an intermittently
# unusable credential, roughly one mint in six, which is the worst possible
# frequency for a bug in an authentication path. Hex has no character in
# common with the separator, which makes the format unambiguous by
# construction rather than by luck. The secret may contain `_` freely: the
# split is on the *first* one, which the fixed-width prefix guarantees is the
# separator.
PREFIX_LENGTH = 12


class ApiKey(RedactedReprMixin, UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "api_key"
    __redacted_fields__: ClassVar[set[str]] = {"key_hash"}
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # The page ApiKeyRepository.list_cursor emits (ORDER BY created_at
        # DESC, id DESC) within one org. There is no status column to put in
        # front of it: a key's state is derived from three timestamps rather
        # than stored, so the list is always the org's whole (capped) set.
        Index("ix_api_key_org_id_created_at_id", "org_id", "created_at", "id"),
    )

    # Declared here rather than inherited from OrgScopedMixin -- the only
    # org-scoped table in the codebase that does not use the mixin, for one
    # reason: ON DELETE CASCADE. T-310 asks that deleting an organization take
    # its keys with it, because a live credential outliving its tenant is the
    # worst orphan this schema could produce, and the product exposes no
    # org-delete endpoint, so the only thing that can delete that row is an
    # operator at a psql prompt. A rule the database enforces holds whether or
    # not that person has read this file.
    #
    # Overriding the mixin's field instead would work at runtime but SQLModel
    # warns on the shadowing (and warnings in this repo are things somebody
    # eventually silences globally), and putting `ondelete` on the mixin would
    # change the delete behaviour of every org-scoped table in the product as
    # a side effect of one table's requirement. Declaring the column is the
    # smallest honest version. It is otherwise identical to the mixin's, which
    # is what keeps BaseRepository's org filter (§0.2) applicable.
    org_id: UUID = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")

    name: str = Field(max_length=100)
    # Unique and indexed: this is the only column the authentication path
    # queries, once per request that presents a key. Everything else about
    # the row -- the scopes, the expiry, the digest to compare -- is read
    # from the row this finds.
    prefix: str = Field(max_length=64, index=True, unique=True)
    key_hash: str = Field(max_length=64)
    # JSONB rather than a join table. Scopes are a closed vocabulary defined
    # in code (app.modules.api_keys.scopes), not rows a product edits, and a
    # key's scope set is only ever read whole, alongside the key itself.
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    # Nullable, per T-036: see the module docstring.
    created_by: UUID | None = Field(default=None, foreign_key="user.id", index=True)
    # Written at most once per key per minute -- see ApiKeyService.touch_used.
    # A write per request on a hot key is an N+1 wearing a different hat.
    last_used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    # None means "never expires", which is a deliberate choice a caller makes
    # rather than the only behaviour available.
    expires_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    # Distinct from SoftDeleteMixin's deleted_at on purpose: `deleted_at`
    # means the row is gone from every list, `revoked_at` means the key is
    # dead but still shown, because "this key was revoked on the 3rd" is the
    # answer somebody needs during an incident.
    revoked_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    # T-312: the per-key override for API_KEY_RATE_LIMIT_PER_MINUTE, so one
    # integration can be raised without raising everyone. None means "use the
    # setting", which is what every key gets until somebody decides otherwise.
    rate_limit_per_minute: int | None = Field(default=None)
