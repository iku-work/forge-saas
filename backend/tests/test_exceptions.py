import pytest

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    PlanLimitError,
    RateLimitError,
    UnprocessableEntityError,
)


@pytest.mark.parametrize(
    ("exc_class", "status_code", "code", "default_message_key"),
    [
        (DomainError, 400, "domain_error", "errors.fallback"),
        (NotFoundError, 404, "not_found", "errors.not_found"),
        (PermissionDeniedError, 403, "permission_denied", "errors.permission_denied"),
        (AuthenticationError, 401, "authentication_failed", "errors.authentication_failed"),
        (ConflictError, 409, "conflict", "errors.conflict"),
        (PlanLimitError, 402, "plan_limit_reached", "errors.plan_limit_reached"),
        (RateLimitError, 429, "rate_limited", "errors.rate_limited"),
        (
            UnprocessableEntityError,
            422,
            "unprocessable_entity",
            "errors.unprocessable_entity",
        ),
    ],
)
def test_domain_error_subclass_status_and_code(
    exc_class: type[DomainError], status_code: int, code: str, default_message_key: str
) -> None:
    exc = exc_class("errors.boom", fallback="boom", details={"k": "v"})

    assert exc.status_code == status_code
    assert exc.code == code
    assert exc.message_key == "errors.boom"
    assert exc.fallback == "boom"
    assert exc.details == {"k": "v"}
    assert exc_class.default_message_key == default_message_key


def test_domain_error_details_default_to_none() -> None:
    exc = DomainError("errors.boom")

    assert exc.details is None


def test_domain_error_params_default_to_empty_dict() -> None:
    exc = DomainError("errors.boom")

    assert exc.params == {}


def test_domain_error_falls_back_to_class_default_message_key_when_omitted() -> None:
    exc = NotFoundError()

    assert exc.message_key == "errors.not_found"


def test_domain_error_fallback_defaults_to_generic_string() -> None:
    exc = DomainError()

    assert exc.fallback == "An error occurred"
    assert exc.message_key == "errors.fallback"
