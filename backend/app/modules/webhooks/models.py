"""
Tables for the `outbound_webhooks` module (T-320): where a product sends its
events, and what happened to every one it sent.

Two tables with very different lifetimes, and the difference is the thing to
hold on to when changing either:

  * `webhook_endpoint` is configuration. One row per URL an organization has
    registered, a handful per org, edited by a person.
  * `webhook_delivery` is a log. One row **per event per endpoint**, written
    by a worker, carrying the payload that was sent. It is the
    fastest-growing table this template will ever add, which is why T-321
    ships a prune job with it rather than after it -- a delivery log with no
    retention policy is a disk-full incident with a date on it.

`secret` ends in `_secret`, so §7's Semgrep rule requires `EncryptedString`,
and that is exactly right here in a way it is not for `ApiKey.key_hash`: this
value has to be *recoverable*, because signing an outbound request means
computing an HMAC with it. A digest would make the column useless. It is
therefore a real secret at rest, encrypted with `ENCRYPTION_KEY` and walked
by T-245's rotation job like every other `EncryptedString` column, and
`RedactedReprMixin` keeps it out of tracebacks and Sentry events.

`secret_previous` is the rotation overlap (T-322): rotating hands the caller
a new secret and keeps the old one valid until `secret_previous_expires_at`,
so a consumer can deploy a verifier that accepts either and then drop the
old one. Without it, rotation is an outage everybody schedules for a Sunday.

`consecutive_failures` and `disabled_at` together are the per-endpoint
circuit breaker T-321 asks for, and they live in the database rather than in
`app.core.resilience`'s `BREAKERS` on purpose: that module keys breakers by
*integration* (one Stripe, one Resend), which is the right shape for a
vendor and the wrong one for an unbounded set of customer-supplied URLs. A
row also survives a worker restart, which an in-process breaker does not.
"""

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.encrypted_type import EncryptedString, RedactedReprMixin
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin

_TZ_DATETIME = DateTime(timezone=True)

# How many consecutive failed deliveries disable an endpoint. Five is the same
# number app.core.resilience gives its breakers, and for the same reason: it
# is comfortably more than a transient blip and comfortably fewer than a day
# of retries against a consumer that is never coming back.
MAX_CONSECUTIVE_FAILURES = 5

# Response bodies are stored so a developer can see *why* their consumer
# rejected an event. Truncated because a consumer that returns a 40 MB HTML
# error page should cost one row of ordinary size, not a table scan's worth
# of TOAST.
MAX_RESPONSE_BODY_CHARS = 2048

# How long a rotated secret keeps working. Long enough that a consumer can
# ship a deploy without coordinating with anyone; short enough that a leaked
# secret is not still valid next week.
ROTATION_OVERLAP_HOURS = 24


class WebhookEndpoint(
    RedactedReprMixin,
    UUIDPKMixin,
    TimestampMixin,
    SoftDeleteMixin,
    SQLModel,
    table=True,
):
    __tablename__ = "webhook_endpoint"
    __redacted_fields__: ClassVar[set[str]] = {"secret", "secret_previous"}
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # The page WebhookEndpointRepository.list_cursor emits (ORDER BY
        # created_at DESC, id DESC) within one org. Endpoints are few per org,
        # but the *dispatch* query -- "every enabled endpoint in this org
        # subscribed to this event" -- runs on every emitted event, and it
        # starts here.
        Index("ix_webhook_endpoint_org_id_created_at_id", "org_id", "created_at", "id"),
    )

    # Declared here instead of inheriting OrgScopedMixin, for the one reason
    # T-310's ApiKey.org_id gives: ON DELETE CASCADE. This row holds a live
    # signing secret, and a credential outliving its tenant is the worst
    # orphan this schema can produce. Without it, deleting an organization
    # does not orphan the endpoint -- it fails outright on the foreign key,
    # which is safe but means "remove this customer" is a puzzle rather than a
    # DELETE. The product exposes no org-delete endpoint, so the only thing
    # that can remove that row is an operator at a psql prompt: a rule the
    # database enforces holds whether or not that person has read this file.
    # Otherwise identical to the mixin's, which is what keeps
    # BaseRepository's org filter (§0.2) applicable.
    org_id: UUID = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")

    url: str = Field(max_length=2048)
    description: str | None = Field(default=None, max_length=500)
    # §7's rule, and the one column in this codebase where encryption rather
    # than hashing is the *requirement* -- see the module docstring.
    secret: str = Field(sa_column=Column(EncryptedString, nullable=False))
    secret_previous: str | None = Field(default=None, sa_column=Column(EncryptedString))
    secret_previous_expires_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # JSONB rather than a join table, for the same reason ApiKey.scopes is:
    # a closed vocabulary defined in code (app.modules.webhooks.events), only
    # ever read whole, alongside the row it belongs to.
    event_types: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    enabled: bool = Field(default=True, index=True)
    # Reset to 0 by any success. `disabled_at` is set when it reaches
    # MAX_CONSECUTIVE_FAILURES, which also flips `enabled` off -- two columns
    # because "an operator turned this off" and "we turned this off for you"
    # are different facts and the UI says so.
    consecutive_failures: int = Field(default=0)
    disabled_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    created_by: UUID | None = Field(default=None, foreign_key="user.id", index=True)


class WebhookDelivery(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    """One attempt-tracked delivery of one event to one endpoint.

    `payload` is captured at **emit** time and stored, which is the single
    most important property of this table: a replay (T-322) must send what
    happened then, not what the row it described says now. A delivery log
    that re-serialises current state is not an audit trail, it is a
    second, worse API.
    """

    __tablename__ = "webhook_delivery"
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # The delivery log's page: one endpoint's attempts, newest first
        # (T-322 pages this by keyset, never OFFSET -- §0.3).
        Index(
            "ix_webhook_delivery_endpoint_id_created_at_id",
            "endpoint_id",
            "created_at",
            "id",
        ),
        # The prune job's sweep (T-321) and the org-wide log, which shares its
        # leading column.
        Index("ix_webhook_delivery_org_id_created_at", "org_id", "created_at"),
        # What the retry sweep asks for: rows that are due. `status` first
        # because `pending` is a small and shrinking slice of a table whose
        # bulk is `delivered`.
        Index("ix_webhook_delivery_status_next_attempt_at", "status", "next_attempt_at"),
    )

    # Same, one level down: an org delete has to reach
    # this table too. `endpoint_id` already cascades, so in practice the
    # endpoint takes its log with it -- but the org FK below is what the
    # database checks first, and without CASCADE on it a tenant delete
    # fails on the log rather than on the endpoint.
    org_id: UUID = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    endpoint_id: UUID = Field(foreign_key="webhook_endpoint.id", index=True, ondelete="CASCADE")
    event_type: str = Field(max_length=100, index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))  # type: ignore[type-arg]
    # "pending" | "delivered" | "failed" | "dead" -- see schemas.DeliveryStatus.
    # A plain string rather than a native enum: adding a state to a Postgres
    # ENUM is a migration with a lock, and this column is on the biggest table
    # in the product.
    status: str = Field(default="pending", max_length=20, index=True)
    attempt_count: int = Field(default=0)
    response_code: int | None = Field(default=None)
    response_body: str | None = Field(default=None, max_length=MAX_RESPONSE_BODY_CHARS)
    error: str | None = Field(default=None, max_length=500)
    next_attempt_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    delivered_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # The value of the `X-Forge-Delivery` header. Retries of the *same*
    # delivery reuse it so a consumer can dedupe; a replay (T-322) is a new
    # row and therefore a new id, because it is a new thing to have happened.
    delivery_id: UUID = Field(index=True)
    # Set on a replay, pointing at the delivery whose stored payload was
    # re-sent. Nullable everywhere else, and never written back onto the
    # original row -- the log is append-only.
    replay_of_id: UUID | None = Field(default=None, foreign_key="webhook_delivery.id", index=True)
