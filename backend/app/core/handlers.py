import logging
from uuid import uuid4

import sentry_sdk
import stripe
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.envelope import Envelope, ErrorBody, Meta
from app.core.exceptions import DomainError
from app.core.i18n import get_default_locale, t

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


def _locale(request: Request) -> str:
    locale: str | None = getattr(request.state, "locale", None)
    return locale or get_default_locale()


def _envelope_response(
    request: Request,
    status_code: int,
    error: ErrorBody,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = Envelope[None](error=error, meta=Meta(request_id=_request_id(request)))
    return JSONResponse(
        status_code=status_code, content=envelope.model_dump(mode="json"), headers=headers
    )


def _translate_or_fallback(message_key: str, locale: str, fallback: str, **params: str) -> str:
    try:
        return t(message_key, locale, **params)
    except KeyError:
        logger.warning("i18n: missing translation key %r, using fallback", message_key)
        return fallback


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101 — Starlette dispatches by registered class
    message = _translate_or_fallback(exc.message_key, _locale(request), exc.fallback, **exc.params)
    error = ErrorBody(
        code=exc.code, message_key=exc.message_key, message=message, details=exc.details
    )
    # T-312: a 429 carries Retry-After and the X-RateLimit-* trio. They are
    # part of the error's meaning and are read by the client's HTTP layer
    # rather than by its JSON parser, which is why they cannot live in the
    # envelope. Every other DomainError leaves `headers` None.
    return _envelope_response(request, exc.status_code, error, exc.headers)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 — same as above
    message = _translate_or_fallback(
        "errors.validation_failed", _locale(request), "Request validation failed."
    )
    error = ErrorBody(
        code="validation_error",
        message_key="errors.validation_failed",
        message=message,
        details={"errors": jsonable_encoder(exc.errors())},
    )
    return _envelope_response(request, 422, error)


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    # T-225/ASVS V14.5.1: FastAPI already 405s a request whose method isn't
    # declared for the matched route (Starlette raises StarletteHTTPException
    # itself, before this ever reaches a route handler) -- this only adds the
    # log line so an operator can alert on repeated wrong-method probes.
    # Every other status this handler sees (404 for no matching route, etc.)
    # falls through to FastAPI's own default response, unchanged.
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 — Starlette dispatches by registered class
    if exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
        logger.warning(
            "405 Method Not Allowed: %s %s (request_id=%s)",
            request.method,
            request.url.path,
            _request_id(request),
        )
    return await _default_http_exception_handler(request, exc)


async def stripe_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Stripe refusing us is not "an unexpected error occurred".

    Without this, every StripeError fell through to the unhandled handler: a
    500, a generic message, and a Sentry event filed as an application bug.
    The most likely cause is not a bug at all -- it is a Plan row whose
    stripe_price_id_* does not exist in the connected account, which is
    exactly what app.core.seed ships (`price_pro_monthly` is a placeholder,
    not an ID). That produced a 500 on POST /billing/checkout-session with
    nothing in the response to suggest looking at the price.

    A CardError is the one kind the *caller* can act on, and Stripe writes
    `user_message` for exactly that purpose, so it is passed through with a
    402. Everything else is our problem, not theirs: the caller gets a
    generic 502 while the operator gets the code, the message and Stripe's
    request id in the log.
    """
    assert isinstance(exc, stripe.StripeError)  # noqa: S101 — Starlette dispatches by registered class
    stripe_request_id = getattr(exc, "request_id", None)
    stripe_code = getattr(exc, "code", None)

    if isinstance(exc, stripe.CardError):
        logger.warning(
            "Stripe declined a card: code=%s request_id=%s", stripe_code, stripe_request_id
        )
        message = exc.user_message or _translate_or_fallback(
            "errors.card_declined", _locale(request), "Your card was declined."
        )
        error = ErrorBody(code="card_declined", message_key="errors.card_declined", message=message)
        return _envelope_response(request, 402, error)

    # Deliberately logged, not raised on to Sentry as an application error:
    # this is an upstream refusal or a misconfiguration, and the detail an
    # operator needs is the Stripe message, which never reaches the client.
    logger.error(
        "Stripe call failed: %s code=%s request_id=%s message=%s",
        type(exc).__name__,
        stripe_code,
        stripe_request_id,
        exc.user_message or str(exc),
    )
    message = _translate_or_fallback(
        "errors.payment_provider_error",
        _locale(request),
        "The payment provider could not complete this request. If it keeps happening, "
        "contact support.",
    )
    error = ErrorBody(
        code="payment_provider_error",
        message_key="errors.payment_provider_error",
        message=message,
    )
    return _envelope_response(request, 502, error)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    sentry_sdk.capture_exception(exc)
    message = _translate_or_fallback(
        "errors.internal_error", _locale(request), "An unexpected error occurred."
    )
    error = ErrorBody(code="internal_error", message_key="errors.internal_error", message=message)
    return _envelope_response(request, 500, error)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    # Before the catch-all Exception handler below, which would otherwise
    # turn an upstream refusal into a 500 filed as an application bug.
    app.add_exception_handler(stripe.StripeError, stripe_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
