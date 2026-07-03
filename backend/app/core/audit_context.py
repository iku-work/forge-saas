"""
Request-scoped ContextVar carrying the acting superadmin's id during a T-142
impersonation session. Set by app.middleware.impersonation's
ImpersonationContextMiddleware (which decodes the bearer token once per
request) and read back by app.modules.audit.service.AuditService.log, so
every audit event written during an impersonated request is stamped with
impersonator_id automatically -- no service or router call site has to
thread the value through by hand.
"""

from contextvars import ContextVar, Token
from uuid import UUID

_impersonator_id: ContextVar[UUID | None] = ContextVar("impersonator_id", default=None)


def get_impersonator_id() -> UUID | None:
    return _impersonator_id.get()


def set_impersonator_id(value: UUID | None) -> Token[UUID | None]:
    return _impersonator_id.set(value)


def reset_impersonator_id(token: Token[UUID | None]) -> None:
    _impersonator_id.reset(token)
