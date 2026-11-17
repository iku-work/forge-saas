"""POST /api/v1/feedback: a signed-in user sends feedback. The inbox that
reads it is under /admin (app.modules.admin.router). Registered with the
admin module: without an admin area nobody could read it."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_user, get_redis, reject_during_impersonation
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import sensitive_limit
from app.modules.auth.models import User
from app.modules.feedback.schemas import FeedbackCreateRequest, FeedbackPublic
from app.modules.feedback.service import FeedbackService

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@sensitive_limit
async def send_feedback(
    request: Request,
    body: FeedbackCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[FeedbackPublic]:
    row = await FeedbackService(session, redis).submit(user, body)
    await session.commit()
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    return Envelope(
        data=FeedbackPublic(id=row.id, created_at=row.created_at),
        meta=Meta(request_id=request_id),
    )
