import logging
from uuid import uuid4

import sentry_sdk
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


def _envelope_response(request: Request, status_code: int, error: ErrorBody) -> JSONResponse:
    envelope = Envelope[None](error=error, meta=Meta(request_id=_request_id(request)))
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


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
    return _envelope_response(request, exc.status_code, error)


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


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    sentry_sdk.capture_exception(exc)
    message = _translate_or_fallback(
        "errors.internal_error", _locale(request), "An unexpected error occurred."
    )
    error = ErrorBody(code="internal_error", message_key="errors.internal_error", message=message)
    return _envelope_response(request, 500, error)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
