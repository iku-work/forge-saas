from typing import Any

from pydantic import BaseModel


class Meta(BaseModel):
    request_id: str
    next_cursor: str | None = None
    version: str = "1.0.0"


class ErrorBody(BaseModel):
    code: str
    # T-218: many distinct failure causes share one coarse `code` (every
    # ConflictError is code="conflict", every AuthenticationError is
    # code="authentication_failed", ...), which is all a web client can key
    # a frontend-side translation off of -- see getErrorMessage.ts's
    # `errors.${code}` lookup. `message_key` (e.g. "errors.email_unchanged")
    # is the specific i18n key this error was actually raised with, letting
    # a client that wants finer-grained copy look that up first and fall
    # back to `code` only if no specific translation exists for it.
    message_key: str
    message: str
    details: dict[str, Any] | None = None


class Envelope[T](BaseModel):
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta
