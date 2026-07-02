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
    ) -> None:
        super().__init__(fallback)
        self.message_key = message_key or self.default_message_key
        self.fallback = fallback
        self.params = params or {}
        self.details = details


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


class RateLimitError(DomainError):
    status_code = 429
    code = "rate_limited"
    default_message_key = "errors.rate_limited"
