"""
Request/response schemas for the __MODULE_NAME__ module (T-250 scaffold).

§0.4, no mass assignment: the request schemas are hand-written lists of the
fields a *caller* may set, never `Item` itself. Nothing here accepts `id`,
`org_id`, `created_by`, `created_at` or `deleted_at` -- a client that sends
them is simply ignored, because there is no field to bind them to. Keep it
that way when you add columns: a new column is only writable from the API
once you have deliberately added it to a Request schema.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.schemas import AppBaseModel

ItemStatus = Literal["active", "archived"]


class ItemCreateRequest(AppBaseModel):
    name: str = Field(min_length=1, max_length=200)
    status: ItemStatus = "active"


class ItemUpdateRequest(AppBaseModel):
    # Every field optional and defaulted to None, so PATCH semantics work:
    # the service writes only the keys the caller actually sent
    # (`model_dump(exclude_unset=True)`), and "field absent" stays
    # distinguishable from "field explicitly set to its default".
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: ItemStatus | None = None


class ItemPublic(AppBaseModel):
    # from_attributes: built with model_validate(item) straight off the ORM
    # row. Response schemas are also an allowlist -- an internal column stays
    # internal until it is listed here.
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: str
    created_by: UUID
    created_at: datetime
