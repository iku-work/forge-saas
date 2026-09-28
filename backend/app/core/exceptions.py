from typing import Any


class DomainError(Exception):
    status_code: int = 400
    code: str = "domain_error"
    default_message_key: str = "errors.fallback"

    def __init__(
        self,
        message_key: str | None = None,
        *,
        fallback: str = "An error occurred",
        params: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(fallback)
        self.message_key = message_key or self.default_message_key
        self.fallback = fallback
        self.params = params or {}
        self.details = details
        # T-312: response headers that are part of the error's meaning rather
        # than of its body -- `Retry-After` and the `X-RateLimit-*` trio on a
        # 429. Everything else about an error is expressible in the envelope;
        # these are not, because a client's HTTP layer is what reads them.
        # app.core.handlers attaches them; every other error leaves this None
        # and the response is unchanged.
        self.headers = headers


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"
    default_message_key = "errors.not_found"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "permission_denied"
    default_message_key = "errors.permission_denied"


class AuthenticationError(DomainError):
    status_code = 401
    code = "authentication_failed"
    default_message_key = "errors.authentication_failed"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"
    default_message_key = "errors.conflict"


class PlanLimitError(DomainError):
    status_code = 402
    code = "plan_limit_reached"
    default_message_key = "errors.plan_limit_reached"


class UnprocessableEntityError(DomainError):
    status_code = 422
    code = "unprocessable_entity"
    default_message_key = "errors.unprocessable_entity"


class InvalidCursorError(DomainError):
    """The `after` cursor on a list endpoint names a row this caller cannot
    page from.

    Raised instead of silently returning page one, which is what every
    cursor implementation here used to do when it could not resolve the
    anchor. An infinite list appends that page rather than replacing it, so
    the user sees the first twenty rows a second time and reads it as
    corruption -- and the bug only appears once the list is long enough to
    paginate, which is exactly when nobody is looking.

    A soft-deleted anchor is *not* this error: its (created_at, id) is still
    a perfectly good position to page from, and the repositories now resolve
    it ignoring `deleted_at`. This is for a cursor that genuinely cannot be
    placed -- a hard-deleted row, another tenant's id, or a forged one.
    """

    status_code = 422
    code = "invalid_cursor"
    default_message_key = "errors.invalid_cursor"


class StepUpAuthRequiredError(DomainError):
    """T-218: the caller has a valid session but it wasn't established (or
    refreshed via a full re-authentication) recently enough for the
    sensitive action being attempted -- distinct from PermissionDeniedError
    so the frontend can react by prompting for re-auth instead of just
    showing an access-denied message."""

    status_code = 403
    code = "step_up_auth_required"
    default_message_key = "errors.step_up_auth_required"


class RateLimitError(DomainError):
    status_code = 429
    code = "rate_limited"
    default_message_key = "errors.rate_limited"


class ExternalServiceUnavailableError(DomainError):
    """Raised by app.core.resilience.resilient() when an integration's
    circuit breaker is open. Names the degraded *feature* (e.g. "Billing"),
    never the vendor internals (e.g. "Stripe") -- callers shouldn't learn
    which third party is having trouble."""

    status_code = 503
    code = "service_unavailable"
    default_message_key = "errors.service_unavailable"

    def __init__(self, feature: str) -> None:
        super().__init__(
            fallback=f"{feature} is temporarily unavailable. Please try again shortly.",
            params={"feature": feature},
        )
