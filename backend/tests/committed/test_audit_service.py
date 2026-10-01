"""
Verifies T-028's AuditService.log(): writes happen inside the caller's own
transaction (visible via flush, before any commit) and a write failure is
swallowed -- logged, never raised -- without poisoning the session for
whatever the caller does next. Uses the same real-Postgres testcontainers
pattern as test_audit_repository.py.
"""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.core.audit_context import reset_impersonator_id, set_impersonator_id
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import AuditService


@pytest.fixture
def service(session: AsyncSession) -> AuditService:
    return AuditService(session)


# --- happy path -------------------------------------------------------------


@pytest.mark.owasp("A09")
async def test_log_writes_row_visible_before_commit(
    service: AuditService, session: AsyncSession
) -> None:
    org_id = uuid4()
    actor_id = uuid4()

    await service.log(
        action="membership.role_changed",
        resource_type="membership",
        resource_id="m-1",
        org_id=org_id,
        actor_id=actor_id,
        diff={"old_role": "member", "new_role": "admin"},
        ip="127.0.0.1",
        ua="pytest",
    )

    row = (
        await session.execute(text("SELECT action, org_id, actor_id, diff FROM audit_event"))
    ).one()
    assert row.action == "membership.role_changed"
    assert str(row.org_id) == str(org_id)
    assert str(row.actor_id) == str(actor_id)
    assert row.diff == {"old_role": "member", "new_role": "admin"}


async def test_log_allows_null_org_id(service: AuditService, session: AsyncSession) -> None:
    await service.log(action="user.registered", resource_type="user", org_id=None)

    row = (await session.execute(text("SELECT org_id FROM audit_event"))).one()
    assert row.org_id is None


# --- failure swallowing -------------------------------------------------------


async def test_log_swallows_repository_failure_without_raising(
    service: AuditService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(**_kwargs: Any) -> AuditEvent:  # noqa: ANN401 -- mirrors AuditRepository.create's signature
        raise RuntimeError("boom")

    monkeypatch.setattr(service._repo, "create", _boom)

    await service.log(action="user.registered", resource_type="user", org_id=None)


async def test_log_failure_does_not_poison_session_for_later_use(
    service: AuditService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(**_kwargs: Any) -> AuditEvent:  # noqa: ANN401 -- mirrors AuditRepository.create's signature
        raise RuntimeError("boom")

    monkeypatch.setattr(service._repo, "create", _boom)
    await service.log(action="user.registered", resource_type="user", org_id=None)

    # The session must still be usable afterward -- the SAVEPOINT rollback
    # inside log() must not have aborted the caller's outer transaction.
    result = await session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


# --- T-142: automatic impersonator_id stamping --------------------------------


async def test_log_stamps_impersonator_id_from_ambient_context(
    service: AuditService, session: AsyncSession
) -> None:
    impersonator_id = uuid4()
    token = set_impersonator_id(impersonator_id)
    try:
        await service.log(action="user.registered", resource_type="user", org_id=None)
    finally:
        reset_impersonator_id(token)

    row = (await session.execute(text("SELECT impersonator_id FROM audit_event"))).one()
    assert str(row.impersonator_id) == str(impersonator_id)


async def test_log_leaves_impersonator_id_null_outside_impersonation_context(
    service: AuditService, session: AsyncSession
) -> None:
    await service.log(action="user.registered", resource_type="user", org_id=None)

    row = (await session.execute(text("SELECT impersonator_id FROM audit_event"))).one()
    assert row.impersonator_id is None
