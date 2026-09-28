"""
DB access for the `outbound_webhooks` module (T-320/T-321) -- the only layer
allowed to build queries (§0.1).

Two repositories, and one of them breaks a rule on purpose:

  * `WebhookEndpointRepository` is an ordinary `BaseRepository`, org-scoped
    and soft-delete-aware like every other one.
  * `WebhookDeliveryRepository` is org-scoped for everything a *request* can
    reach, and additionally exposes two methods that are not
    (`list_due`, `prune_older_than`). Those are the worker's: the retry sweep
    and the retention job run across every tenant by definition, and pretending
    otherwise would mean N queries for N organizations. They are named so the
    difference is visible at the call site, and neither is reachable from a
    router.

The delivery log is paged by **keyset, never OFFSET** (§0.3, and T-322 says so
again): `ix_webhook_delivery_endpoint_id_created_at_id` is the index that
makes page 500 cost what page 1 costs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, select, tuple_

from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, BaseRepository

from .models import WebhookDelivery, WebhookEndpoint

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession

# One batch of the retention sweep. Small enough that the DELETE never holds a
# long lock on the biggest table in the product, which is the whole reason the
# job deletes in batches at all rather than issuing one statement.
PRUNE_BATCH_SIZE = 1000


class WebhookEndpointRepository(BaseRepository[WebhookEndpoint]):
    model = WebhookEndpoint

    async def list_subscribed(self, event_type: str) -> list[WebhookEndpoint]:
        """Every enabled endpoint in this org subscribed to `event_type`.

        The `?|` containment operator would be the JSONB-native spelling, but
        `event_types` is a small array on a small table and the set of
        endpoints per org is bounded by configuration rather than by usage --
        so this filters in Python after an indexed read of the org's enabled
        rows, which keeps the statement portable and the plan obvious.
        """
        m = cast("Any", WebhookEndpoint)
        stmt = select(WebhookEndpoint).where(
            m.org_id == self.org_id,
            m.deleted_at.is_(None),
            m.enabled.is_(True),
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [row for row in rows if event_type in row.event_types]

    async def count_all(self) -> int:
        m = cast("Any", WebhookEndpoint)
        stmt = (
            select(func.count())
            .select_from(WebhookEndpoint)
            .where(m.org_id == self.org_id, m.deleted_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one()


class WebhookDeliveryRepository(BaseRepository[WebhookDelivery]):
    model = WebhookDelivery

    async def list_for_endpoint(
        self,
        endpoint_id: UUID,
        after: UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        status: str | None = None,
    ) -> tuple[list[WebhookDelivery], UUID | None]:
        """One endpoint's delivery log, newest first, by keyset.

        Not `BaseRepository.list_cursor` with a filter bolted on, because the
        anchor read has to be org-scoped *and* endpoint-scoped: resolving a
        cursor that belongs to another endpoint would silently page from the
        wrong place rather than fail.
        """
        capped = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", WebhookDelivery)
        stmt = (
            select(WebhookDelivery)
            .where(
                m.org_id == self.org_id,
                m.endpoint_id == endpoint_id,
                m.deleted_at.is_(None),
            )
            .order_by(m.created_at.desc(), m.id.desc())
            .limit(capped + 1)
        )
        if status is not None:
            stmt = stmt.where(m.status == status)
        if after is not None:
            # get_cursor_anchor, not get(): a soft-deleted delivery is still a
            # valid position to page past, and an unresolvable cursor is an
            # error rather than a silent reset to page one.
            anchor = await self.get_cursor_anchor(after)
            stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        rows = list((await self.session.execute(stmt)).scalars().all())
        page = rows[:capped]
        next_cursor = page[-1].id if page and len(rows) > capped else None
        return page, next_cursor

    async def create_for_endpoint(self, **kwargs: Any) -> WebhookDelivery:  # noqa: ANN401 -- model ctor
        """`BaseRepository.create` stamps `org_id` from the repository, which
        is exactly what is wanted -- this exists only so the worker's call
        site reads as what it is."""
        return await self.create(**kwargs)

    # --- worker-side, deliberately not org-scoped -----------------------------

    @staticmethod
    async def list_due(
        session: AsyncSession, now: datetime, limit: int = 100
    ) -> list[WebhookDelivery]:
        """Pending deliveries whose next attempt is due, across every tenant.

        A `staticmethod` taking a session rather than an instance method, so
        that "this one is not org-scoped" is impossible to miss at the call
        site and impossible to reach from a router (which only ever holds a
        constructed, org-scoped repository).
        """
        m = cast("Any", WebhookDelivery)
        stmt = (
            select(WebhookDelivery)
            .where(
                m.status == "pending",
                m.deleted_at.is_(None),
                m.next_attempt_at.is_not(None),
                m.next_attempt_at <= now,
            )
            .order_by(m.next_attempt_at)
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def prune_older_than(session: AsyncSession, cutoff: datetime) -> int:
        """Hard-delete one batch's worth of deliveries older than `cutoff`.

        Hard, not soft: this table's entire problem is that it grows without
        bound, and a soft delete grows it further. Batched by primary key
        through a subquery rather than `DELETE ... LIMIT` (which Postgres does
        not have), so each statement takes a bounded number of row locks and a
        long retention backlog drains over several ticks instead of holding
        one lock for minutes.
        """
        m = cast("Any", WebhookDelivery)
        doomed = (
            select(m.id)
            .where(m.created_at < cutoff)
            .order_by(m.created_at)
            .limit(PRUNE_BATCH_SIZE)
            .scalar_subquery()
        )
        # CursorResult carries `rowcount`; the Result the stub declares does
        # not, and a DELETE always produces the former.
        result = cast(
            "CursorResult[Any]",
            await session.execute(delete(WebhookDelivery).where(m.id.in_(doomed))),
        )
        return result.rowcount or 0

    @staticmethod
    async def count_pending(session: AsyncSession) -> int:
        """Queue depth, for the gauge T-321 publishes to Redis.

        Deliberately a count of *rows awaiting delivery* rather than of ARQ
        jobs: a delivery whose job has been consumed but whose retry is
        scheduled for two hours from now is still work this product owes
        somebody, and `forge_arq_queue_depth` cannot see it.
        """
        m = cast("Any", WebhookDelivery)
        stmt = (
            select(func.count())
            .select_from(WebhookDelivery)
            .where(m.status == "pending", m.deleted_at.is_(None))
        )
        return (await session.execute(stmt)).scalar_one()
