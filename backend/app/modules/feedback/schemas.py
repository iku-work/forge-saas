"""Request/response schemas for feedback; the admin inbox's are in
app.modules.admin.schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import AppBaseModel

FeedbackCategory = Literal["bug", "idea", "other"]


class FeedbackCreateRequest(AppBaseModel):
    category: FeedbackCategory
    message: str = Field(min_length=1, max_length=5000)
    # The app path the form was opened on. A path only: no scheme, host,
    # query string or fragment, which could carry a token.
    page: str | None = Field(default=None, max_length=500, pattern=r"^/[^?#\s]*$")
    # The org the sender was working in, if any. Must be one of theirs.
    org_id: UUID | None = None


class FeedbackPublic(AppBaseModel):
    id: UUID
    created_at: datetime
