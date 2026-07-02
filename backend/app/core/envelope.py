from typing import Any

from pydantic import BaseModel


class Meta(BaseModel):
    request_id: str
    next_cursor: str | None = None
    version: str = "1.0.0"


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class Envelope[T](BaseModel):
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta
