"""Shared Pydantic base for every request/response schema in app.modules.*."""

from typing import Any

from pydantic import BaseModel, model_validator


def _check_for_null_byte(value: object) -> None:
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("must not contain a null byte")
    elif isinstance(value, dict):
        for item in value.values():
            _check_for_null_byte(item)
    elif isinstance(value, list):
        for item in value:
            _check_for_null_byte(item)


class AppBaseModel(BaseModel):
    """
    Every schema in app.modules.* inherits from this instead of plain
    `pydantic.BaseModel`. Postgres's UTF8 encoding rejects a literal `\\x00`
    in *any* string parameter sent over the wire -- not just ones being
    stored; even a `WHERE col = :param` lookup trips
    `asyncpg.CharacterNotInRepertoireError` (found across three unrelated
    fields by T-050's schemathesis contract test: org name, GDPR consent
    type/version, billing plan_slug). That's an unhandled `DBAPIError`, not
    a `DomainError`, so it surfaces as a raw 500 instead of a 422.
    `Field(min_length=...)` doesn't help -- a null byte is one character
    like any other for length-counting purposes.

    The `isinstance(data, dict)` guard below means this only fires for
    request bodies built from raw JSON (or keyword construction, which
    Pydantic v2 treats the same way) -- it's a no-op for response schemas
    built via `model_validate(orm_obj, from_attributes=True)`, since the raw
    input there is the ORM object, not a dict.
    """

    @model_validator(mode="before")
    @classmethod
    def _reject_null_bytes(cls, data: Any) -> Any:  # noqa: ANN401 -- matches pydantic's own mode="before" signature
        if isinstance(data, dict):
            _check_for_null_byte(data)
        return data
