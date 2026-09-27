"""
Business logic for the `outbound_webhooks` module (T-320/T-321/T-322).

Three responsibilities, deliberately in one file because they share the same
invariants about secrets and about what an endpoint is allowed to be:

  * **`WebhookEndpointService`** -- managing endpoints: create, edit, rotate
    the signing secret, disable, delete.
  * **`WebhookEmitter`** -- turning "this happened" into one `WebhookDelivery`
    row per subscribed endpoint, plus a job per row. Called from the
    product's own code, never from a request body.
  * **`WebhookDeliveryService`** -- what the delivery job records afterwards:
    success, failure, the retry schedule, and the auto-disable.

Nothing here commits; the router (or the job) owns the transaction boundary.

## The two rules that are not obvious

**SSRF is a delivery-time problem, not a save-time one** (T-320, and T-212's
`url_guard` was built for exactly this caller). A hostname that resolved to a
public address when the endpoint was saved can resolve to `169.254.169.254`
tomorrow -- DNS is the attacker's to change. So `validate_outbound_url` runs
when the URL is saved *and* again inside the job, immediately before the
request. Only the second one is load-bearing; the first is there so a
misconfiguration is a 422 in a form rather than a silent failure in a log.

**A sibling module may be off.** Auto-disabling an endpoint notifies the org's
owners, and `notifications` is optional -- so that path is guarded rather than
assumed. `emit` is guarded from the other side too: with `outbound_webhooks`
off there is nothing to emit to, and the product's own call sites must not
have to know that.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import select

from app.core import jobs
from app.core.exceptions import (
    ConflictError,
    PlanLimitError,
    UnprocessableEntityError,
)
from app.core.module_loader import is_enabled
from app.core.url_guard import UnsafeOutboundURLError, validate_outbound_url
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.permissions import MemberRole
from app.modules.tenancy.models import Membership

from .events import (
    EventType,
    UnknownEventTypeError,
    WebhookEventEnvelope,
    parse_event_type,
    validate_payload,
)
from .models import (
    MAX_CONSECUTIVE_FAILURES,
    ROTATION_OVERLAP_HOURS,
    WebhookDelivery,
    WebhookEndpoint,
)
from .repository import WebhookDeliveryRepository, WebhookEndpointRepository

if TYPE_CHECKING:
    from pydantic import BaseModel
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from .schemas import WebhookEndpointCreateRequest, WebhookEndpointUpdateRequest

RESOURCE_TYPE = "webhook_endpoint"

# A cap for the same reason T-310 caps API keys at 50: a bounded list needs no
# virtualization, and an org with 500 endpoints is a misconfiguration rather
# than a use case. Enforced here as well as rendered in the UI -- a cap only
# the UI knows about is not a cap.
MAX_ENDPOINTS_PER_ORG = 20

# 32 bytes of CSPRNG output, presented once. Long enough that the HMAC key has
# more entropy than the digest it produces, which is the only bar a signing
# secret has to clear.
SECRET_BYTES = 32
# S105 would read this as a hardcoded credential. It is the opposite: a
# constant, public *prefix* on a value generated per endpoint, and it is here
# so a leaked secret is greppable by a scanner.
SECRET_PREFIX = "whsec_"  # noqa: S105

# T-321's retry ladder, in order. The last entry is not "give up in 12 hours"
# -- it is the wait *before* the final attempt, after which the delivery is
# dead-lettered. Five retries spanning ~14h 36m: long enough that a consumer
# whose on-call woke up in the night still receives the event, short enough
# that the row is not pending for a week.
RETRY_SCHEDULE: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)
MAX_ATTEMPTS = len(RETRY_SCHEDULE) + 1


def generate_secret() -> str:
    """`whsec_<43 urlsafe chars>`. The prefix is not decoration: a fixed,
    searchable prefix is what makes a leaked signing secret findable by a
    secret scanner in a public repository -- the same argument
    `ApiKey.KEY_PREFIX` makes, and the reason both are worth copying from
    Stripe."""
    return SECRET_PREFIX + secrets.token_urlsafe(SECRET_BYTES)


def next_attempt_after(attempts_made: int, now: datetime | None = None) -> datetime | None:
    """When the next attempt should run, given how many have already been
    made -- or None when there is no next one and the delivery is dead.

    Indexed by `attempts_made - 1`, not by `attempts_made`: after the *first*
    attempt fails, the wait is the *first* rung (one minute). Getting that
    off by one silently skips a rung and makes the published ladder a lie,
    which is why `test_the_whole_ladder_is_the_documented_one` measures every
    gap rather than only the last.
    """
    if attempts_made < 1 or attempts_made > len(RETRY_SCHEDULE):
        return None
    return (now or utcnow()) + RETRY_SCHEDULE[attempts_made - 1]


class WebhookEndpointService:
    """Management operations. Org-scoped, like the repository behind it."""

    def __init__(self, session: AsyncSession, org_id: UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = WebhookEndpointRepository(session, org_id)
        self._audit = AuditService(session)

    async def create_endpoint(
        self, user_id: UUID, payload: WebhookEndpointCreateRequest
    ) -> tuple[WebhookEndpoint, str]:
        if await self._repo.count_all() >= MAX_ENDPOINTS_PER_ORG:
            raise PlanLimitError(
                "errors.webhook_endpoint_limit_reached",
                fallback=f"This organization is limited to {MAX_ENDPOINTS_PER_ORG} endpoints",
                params={"limit": str(MAX_ENDPOINTS_PER_ORG)},
            )
        url = await self._validated_url(payload.url)
        event_types = self._parse_event_types(payload.event_types)
        secret = generate_secret()
        endpoint = await self._repo.create(
            url=url,
            description=payload.description,
            secret=secret,
            event_types=[event.value for event in event_types],
            created_by=user_id,
        )
        await self._log(
            "webhook_endpoint.created",
            user_id,
            endpoint.id,
            diff={"url": url, "event_types": endpoint.event_types},
        )
        return endpoint, secret

    async def list_endpoints(
        self, after: UUID | None, limit: int
    ) -> tuple[list[WebhookEndpoint], UUID | None]:
        return await self._repo.list_cursor(after, limit)

    async def get_endpoint(self, endpoint_id: UUID) -> WebhookEndpoint:
        return await self._repo.get_or_raise(endpoint_id)

    async def update_endpoint(
        self, user_id: UUID, endpoint_id: UUID, payload: WebhookEndpointUpdateRequest
    ) -> WebhookEndpoint:
        changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            raise UnprocessableEntityError(
                "errors.no_fields_to_update", fallback="No fields to update"
            )
        if "url" in changes and changes["url"] is not None:
            changes["url"] = await self._validated_url(changes["url"])
        if "event_types" in changes and changes["event_types"] is not None:
            changes["event_types"] = [
                event.value for event in self._parse_event_types(changes["event_types"])
            ]
        # Re-enabling by hand is the operator's answer to an auto-disable, and
        # it has to clear the counter too -- otherwise the next single failure
        # trips the breaker again immediately, which reads as "your fix did
        # not work" when in fact nothing was ever retried.
        if changes.get("enabled") is True:
            changes["consecutive_failures"] = 0
            changes["disabled_at"] = None
        endpoint = await self._repo.update(endpoint_id, **changes)
        await self._log("webhook_endpoint.updated", user_id, endpoint_id, diff=changes)
        return endpoint

    async def rotate_secret(self, user_id: UUID, endpoint_id: UUID) -> tuple[WebhookEndpoint, str]:
        """A new signing secret, with the old one still valid for the overlap.

        Both secrets sign every request during the window (see
        `signing.signature_headers`), so a consumer can deploy a verifier that
        accepts either, switch, and then drop the old one -- without a moment
        where correctly-signed requests are rejected. The UI says the deadline
        out loud because a rotation whose window a consumer cannot see is one
        they have to guess at.
        """
        endpoint = await self._repo.get_or_raise(endpoint_id)
        expires_at = utcnow() + timedelta(hours=ROTATION_OVERLAP_HOURS)
        new_secret = generate_secret()
        endpoint = await self._repo.update(
            endpoint_id,
            secret=new_secret,
            secret_previous=endpoint.secret,
            secret_previous_expires_at=expires_at,
        )
        # No `diff` carrying either secret, obviously -- and not a hash of one
        # either. "The secret was rotated, at this time, by this person" is
        # the whole of what an audit trail needs.
        await self._log(
            "webhook_endpoint.secret_rotated",
            user_id,
            endpoint_id,
            diff={"previous_secret_expires_at": expires_at.isoformat()},
        )
        return endpoint, new_secret

    async def delete_endpoint(self, user_id: UUID, endpoint_id: UUID) -> None:
        await self._repo.get_or_raise(endpoint_id)
        await self._repo.soft_delete(endpoint_id)
        await self._log("webhook_endpoint.deleted", user_id, endpoint_id)

    async def _validated_url(self, url: str) -> str:
        try:
            return (await validate_outbound_url(url)).url
        except UnsafeOutboundURLError as exc:
            raise UnprocessableEntityError(
                "errors.unsafe_webhook_url",
                fallback=f"This URL cannot be used as a webhook endpoint: {exc}",
                params={"reason": str(exc)},
            ) from exc

    def _parse_event_types(self, values: list[str]) -> list[EventType]:
        parsed: list[EventType] = []
        for value in values:
            try:
                parsed.append(parse_event_type(value))
            except UnknownEventTypeError as exc:
                raise UnprocessableEntityError(
                    "errors.unknown_event_type",
                    fallback=f"Unknown event type: {value}",
                    params={"event_type": value},
                ) from exc
        # dict.fromkeys rather than set(): duplicates are dropped and the
        # caller's order survives, which is what the UI's checkbox list shows
        # back to them.
        return list(dict.fromkeys(parsed))

    async def _log(
        self,
        action: str,
        user_id: UUID | None,
        endpoint_id: UUID,
        diff: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.log(
            action=action,
            resource_type=RESOURCE_TYPE,
            resource_id=str(endpoint_id),
            org_id=self._org_id,
            actor_id=user_id,
            diff=diff,
        )


class WebhookEmitter:
    """Turns a product event into delivery rows and the jobs that send them.

    The call site is ordinary product code -- a tenancy service that just
    added a member, say -- so this has to be safe to call unconditionally.
    With the module off it is a no-op; with no subscribed endpoint it is a
    no-op; and it never raises for a reason the caller could not have
    prevented, because a webhook failing must not roll back the thing that
    happened.
    """

    def __init__(self, session: AsyncSession, org_id: UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._endpoints = WebhookEndpointRepository(session, org_id)
        self._deliveries = WebhookDeliveryRepository(session, org_id)

    async def emit(self, event_type: EventType, payload: BaseModel) -> list[WebhookDelivery]:
        """One `WebhookDelivery` per subscribed endpoint, each with its job.

        `validate_payload` raises `TypeError` when the payload does not match
        what the registry says this event carries. That is deliberately *not*
        caught: only this codebase emits, so it is a programming error, and
        swallowing it would ship a malformed public contract to every
        subscriber instead of failing a test.
        """
        # Validated *before* the module check, deliberately: a call site that
        # hands `emit` the wrong payload model for its event is a programming
        # error, and one that only raised where the module happens to be
        # enabled would sit undetected in the committed configuration until
        # some product turned webhooks on. This way the existing suite catches
        # it at every call site it already exercises.
        data = validate_payload(event_type, payload)
        if not is_enabled("outbound_webhooks"):
            return []
        endpoints = await self._endpoints.list_subscribed(event_type.value)
        if not endpoints:
            return []

        created: list[WebhookDelivery] = []
        for endpoint in endpoints:
            delivery = await self._deliveries.create_for_endpoint(
                endpoint_id=endpoint.id,
                event_type=event_type.value,
                payload=self._envelope(event_type, data),
                status="pending",
                next_attempt_at=utcnow(),
                delivery_id=uuid4(),
            )
            created.append(delivery)
            await jobs.enqueue_job("deliver_webhook", delivery_id=str(delivery.id))
        return created

    def _envelope(self, event_type: EventType, data: dict[str, Any]) -> dict[str, Any]:
        return WebhookEventEnvelope(
            id=uuid4(),
            type=event_type.value,
            created_at=utcnow(),
            org_id=self._org_id,
            data=data,
        ).model_dump(mode="json")


async def emit(
    session: AsyncSession, org_id: UUID, event_type: EventType, payload: BaseModel
) -> None:
    """The product's own call sites' entry point.

    A module-level function rather than a class, because every caller is a
    service that already has a session and an org and wants one line, not an
    object. It is safe to call unconditionally -- `WebhookEmitter.emit`
    returns immediately when the module is off -- but a caller that has to
    *query* something to build the payload should check
    `is_enabled("outbound_webhooks")` first, so a product with the module off
    pays nothing at all. `tests/modules/test_webhook_events.py` pins that
    every registered event has a call site somewhere in `app/`: a catalogue
    entry nothing emits is a promise the published docs cannot keep.
    """
    await WebhookEmitter(session, org_id).emit(event_type, payload)


class WebhookDeliveryService:
    """The delivery job's half: reading the log, recording outcomes, replaying.

    Org-scoped, because both of its request-facing callers (the log page and
    the replay button) are. The job constructs one per delivery from the
    delivery's own `org_id`, which is the row it already loaded.
    """

    def __init__(self, session: AsyncSession, org_id: UUID, redis: Redis | None = None) -> None:
        self._session = session
        self._org_id = org_id
        # Only the auto-disable path uses it, and only when `notifications` is
        # enabled -- so it is optional, and the request-side callers (the log
        # page, replay) pass nothing.
        self._redis = redis
        self._repo = WebhookDeliveryRepository(session, org_id)
        self._endpoints = WebhookEndpointRepository(session, org_id)
        self._audit = AuditService(session)

    async def list_deliveries(
        self, endpoint_id: UUID, after: UUID | None, limit: int, status: str | None
    ) -> tuple[list[WebhookDelivery], UUID | None]:
        # Proves the endpoint belongs to this org before paging its log --
        # otherwise a delivery list is reachable by guessing an endpoint id,
        # which is the cross-tenant read §0.2 exists to prevent.
        await self._endpoints.get_or_raise(endpoint_id)
        return await self._repo.list_for_endpoint(endpoint_id, after, limit, status)

    async def get_delivery(self, delivery_id: UUID) -> WebhookDelivery:
        return await self._repo.get_or_raise(delivery_id)

    async def replay(self, user_id: UUID, delivery_id: UUID) -> WebhookDelivery:
        """A **new** delivery carrying the original's stored payload.

        The original row is never touched -- not its status, not its
        `delivery_id`, not its attempt count. A delivery log that a replay can
        rewrite is not an audit trail, and "we retried it and now it says
        delivered" is the exact sentence an incident review must not be able
        to produce.

        The new row gets a fresh `delivery_id` so a consumer's idempotency
        key sees it as a new event, which it is: retries of one delivery share
        an id precisely so they can be deduped, and a replay is a deliberate
        request to process the thing again.
        """
        original = await self._repo.get_or_raise(delivery_id)
        endpoint = await self._endpoints.get_or_raise(original.endpoint_id)
        if not endpoint.enabled:
            raise ConflictError(
                "errors.webhook_endpoint_disabled",
                fallback="Enable this endpoint before replaying a delivery",
            )
        replayed = await self._repo.create_for_endpoint(
            endpoint_id=original.endpoint_id,
            event_type=original.event_type,
            payload=original.payload,
            status="pending",
            next_attempt_at=utcnow(),
            delivery_id=uuid4(),
            replay_of_id=original.id,
        )
        await self._audit.log(
            action="webhook_delivery.replayed",
            resource_type="webhook_delivery",
            resource_id=str(replayed.id),
            org_id=self._org_id,
            actor_id=user_id,
            diff={"replay_of": str(original.id), "event_type": original.event_type},
        )
        await jobs.enqueue_job("deliver_webhook", delivery_id=str(replayed.id))
        return replayed

    async def record_success(self, delivery: WebhookDelivery, status_code: int, body: str) -> None:
        await self._repo.update(
            delivery.id,
            status="delivered",
            attempt_count=delivery.attempt_count + 1,
            response_code=status_code,
            response_body=body,
            error=None,
            next_attempt_at=None,
            delivered_at=utcnow(),
        )
        # A success is what closes the breaker, and it has to reset the
        # *counter*, not decrement it: five failures spread over a week with
        # successes in between is a flaky consumer, not a dead one.
        endpoint = await self._endpoints.get_or_raise(delivery.endpoint_id)
        if endpoint.consecutive_failures:
            await self._endpoints.update(endpoint.id, consecutive_failures=0)

    async def record_failure(
        self,
        delivery: WebhookDelivery,
        *,
        status_code: int | None,
        body: str | None,
        error: str,
    ) -> WebhookDelivery:
        """Records one failed attempt and schedules (or abandons) the next.

        Returns the updated row so the job can log what happened without
        re-reading it.
        """
        attempt_count = delivery.attempt_count + 1
        next_attempt = next_attempt_after(attempt_count)
        # "failed" is a delivery that will be tried again; "dead" is one that
        # will not. Two words rather than one plus a nullable timestamp,
        # because the filter a developer actually wants on the log page is
        # "show me what is never going to arrive".
        status = "pending" if next_attempt is not None else "dead"
        updated = await self._repo.update(
            delivery.id,
            status=status,
            attempt_count=attempt_count,
            response_code=status_code,
            response_body=body,
            error=error[:500],
            next_attempt_at=next_attempt,
        )
        if next_attempt is None:
            await self._register_endpoint_failure(delivery.endpoint_id)
        return updated

    async def _register_endpoint_failure(self, endpoint_id: UUID) -> None:
        """One dead-lettered delivery counts once against the endpoint.

        Counted at dead-letter rather than per attempt on purpose: one
        consumer restart produces five failed *attempts* of the same delivery,
        and an endpoint disabled by that is an endpoint disabled by a deploy.
        `MAX_CONSECUTIVE_FAILURES` distinct events that all gave up is a
        consumer that is gone.
        """
        endpoint = await self._endpoints.get_or_raise(endpoint_id)
        failures = endpoint.consecutive_failures + 1
        changes: dict[str, Any] = {"consecutive_failures": failures}
        if failures >= MAX_CONSECUTIVE_FAILURES and endpoint.enabled:
            changes["enabled"] = False
            changes["disabled_at"] = utcnow()
        await self._endpoints.update(endpoint_id, **changes)
        if changes.get("enabled") is False:
            logger.warning(
                "webhooks.endpoint_auto_disabled: endpoint_id={} org_id={} failures={}",
                endpoint_id,
                self._org_id,
                failures,
            )
            await self._audit.log(
                action="webhook_endpoint.auto_disabled",
                resource_type=RESOURCE_TYPE,
                resource_id=str(endpoint_id),
                org_id=self._org_id,
                actor_id=None,
                diff={"consecutive_failures": failures},
                severity="warning",
            )
            await self._notify_owners(endpoint)

    async def _notify_owners(self, endpoint: WebhookEndpoint) -> None:
        """Tells the org's owners their integration stopped, if there is a
        notifications module to tell them with.

        Guarded rather than assumed: `notifications` is optional, and a
        product that turned it off must not get an ImportError-shaped outage
        out of a webhook that failed. Silence is the correct degradation --
        the endpoint is still visibly disabled in the UI and the audit log
        still records why.
        """
        if self._redis is None or not is_enabled("notifications"):
            return
        # Imported here rather than at module scope: `notifications` may be
        # off, and app.modules.webhooks must not pull a disabled module's
        # package in at import time (§0.1's cross-module rule, and the same
        # laziness build_router() uses).
        from app.modules.notifications.service import NotificationService  # noqa: PLC0415

        owners = await self._owner_ids()
        if not owners:
            return
        await NotificationService(self._session, self._redis).send_many(
            user_ids=owners,
            org_id=self._org_id,
            type="webhook_endpoint_disabled",
            params={"url": endpoint.url},
            action_url="/settings/webhooks",
            # One notification per endpoint per disabling, not per failure:
            # the counter reaching its limit is a single event and the id is
            # what makes a retried job idempotent about saying so.
            #
            # Concatenation, not an f-string: `f"prefix:{value}"` is exactly
            # the shape tests/test_cache_keys.py's sweep flags as a
            # hand-built Redis key, and this is a notification dedup key with
            # no cache to invalidate. `get_support_config` dodges the same
            # sweep the same way, for the same reason.
            idempotency_key="webhook_endpoint_disabled:" + str(endpoint.id),
        )

    async def _owner_ids(self) -> list[UUID]:
        m = cast("Any", Membership)
        stmt = select(m.user_id).where(m.org_id == self._org_id, m.role == MemberRole.OWNER)
        return list((await self._session.execute(stmt)).scalars().all())

    def active_secrets(self, endpoint: WebhookEndpoint) -> list[str]:
        """Every secret a request to this endpoint should be signed with.

        One normally; two during a rotation overlap that has not expired. The
        expiry is checked here rather than swept by a job because there is
        nothing to clean up -- an expired `secret_previous` simply stops being
        used, and a sweep would be a scheduled job whose only effect is to
        make a column null.
        """
        secrets_in_use = [endpoint.secret]
        if (
            endpoint.secret_previous is not None
            and endpoint.secret_previous_expires_at is not None
            and endpoint.secret_previous_expires_at > utcnow()
        ):
            secrets_in_use.append(endpoint.secret_previous)
        return secrets_in_use
