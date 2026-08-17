"""
UsageRecord (T-132): the write side of usage-based billing. A feature that
wants to meter itself calls UsageService.record() (directly, or via the
record_usage FastAPI dependency in app.modules.billing.dependencies) once per
billable event; app.workers.jobs.usage.report_usage_to_stripe sweeps
unreported rows every 15 minutes and pushes them to Stripe as usage records.

Not OrgScopedMixin + SoftDeleteMixin like most tables: UsageRecord is an
append-only ledger (no update, no delete -- correcting a bad record means
writing a compensating one, same as an accounting journal), so there's
nothing to soft-delete. It does still carry org_id (via OrgScopedMixin) since
usage is always attributed to an org.

idempotency_key is a genuine DB-level unique constraint, not the app-level
"skip if logged recently" pattern app.modules.audit.service.AuditService uses
for the dunning/trial-warning email markers (T-130/T-131) -- a caller that
retries a request (e.g. a flaky client re-POSTing the same metered action)
should never be able to double-record it, and unlike those markers there is
no fixed replay window to bound how long the key needs to stay unique for.
"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, Index
from sqlmodel import Field, SQLModel

from app.models._mixins import OrgScopedMixin, TimestampMixin, UUIDPKMixin

_TZ_DATETIME = DateTime(timezone=True)


class UsageRecord(UUIDPKMixin, TimestampMixin, OrgScopedMixin, SQLModel, table=True):
    __tablename__ = "usage_record"
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # The read path report_usage_to_stripe actually runs: unreported
        # records grouped by (org_id, metric). Not covered by the individual
        # single-column indexes below.
        #
        # T-132's migration created this index but never declared it here, so
        # autogenerate proposed dropping it on every run -- the index was one
        # `alembic upgrade` away from being silently removed by whoever
        # applied the next generated script without reading it.
        Index(
            "ix_usage_record_reported_to_stripe_at_org_id_metric",
            "reported_to_stripe_at",
            "org_id",
            "metric",
        ),
    )

    metric: str = Field(index=True)  # e.g. "api_calls", "ai_tokens", "exports"
    quantity: int  # always non-negative; multiply up for fractional units
    occurred_at: datetime = Field(index=True, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    reported_to_stripe_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    stripe_usage_record_id: str | None = Field(default=None)
    idempotency_key: str | None = Field(default=None, unique=True, index=True)
