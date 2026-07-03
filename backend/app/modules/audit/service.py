"""
AuditService (T-028) — the only way auth/tenancy services write audit rows;
nothing should import AuditRepository directly (§0.1: cross-module access
goes through services).

The write happens in a SAVEPOINT (`session.begin_nested()`) nested inside the
caller's own transaction: on success it's part of that same transaction (and
commits or rolls back with it), but on failure the savepoint alone unwinds —
the caller's other changes stay intact and committable. Combined with the
broad except below, an audit failure can never take down the business
transaction that triggered it.
"""

import logging
from typing import Any
from uuid import UUID

import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import get_impersonator_id
from app.modules.audit.repository import AuditRepository

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AuditRepository(session)

    async def log(  # noqa: PLR0913 — matches the T-028 spec's exact field set
        self,
        *,
        action: str,
        resource_type: str,
        org_id: UUID | None,
        actor_id: UUID | None = None,
        resource_id: str | None = None,
        diff: dict[str, Any] | None = None,
        ip: str | None = None,
        ua: str | None = None,
        severity: str = "info",
    ) -> None:
        try:
            async with self._session.begin_nested():
                await self._repo.create(
                    action=action,
                    resource_type=resource_type,
                    org_id=org_id,
                    actor_id=actor_id,
                    resource_id=resource_id,
                    diff=diff,
                    ip_address=ip,
                    user_agent=ua,
                    severity=severity,
                    # T-142: automatic, not caller-supplied -- see
                    # app.core.audit_context's own docstring.
                    impersonator_id=get_impersonator_id(),
                )
        except Exception as exc:  # noqa: BLE001 — audit failures must never roll back the caller's business transaction (§T-028)
            logger.warning("audit.write_failed: action=%s resource_type=%s", action, resource_type)
            sentry_sdk.capture_exception(exc)

    async def has_logged(self, *, action: str, resource_id: str) -> bool:
        # T-130: lets an at-least-once background step (a dunning email day)
        # check its own audit trail before re-sending on a retried run.
        return await self._repo.exists(action=action, resource_id=resource_id)
