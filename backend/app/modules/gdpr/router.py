"""
HTTP layer for GDPR self-service endpoints (T-033): data export requests, the
right-to-be-forgotten delete-me flow, and the consent log. Every route is
user-scoped via Depends(get_current_user), not org-scoped -- per §0.2 these
tables belong to the user, not an org, the same exception as
app.modules.auth.models.User/Session/OAuthAccount/TOTPSecret.

Every mutating endpoint commits its own AsyncSession explicitly -- see
app.modules.auth.router's docstring for why the router (not get_session)
owns the transaction boundary.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_user, get_redis, invalidate_user_cache
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.auth.models import User
from app.modules.gdpr.schemas import (
    ConsentRecordPublic,
    DataExportJobPublic,
    DeleteMeRequest,
    ExportJobCreated,
    RecordConsentRequest,
)
from app.modules.gdpr.service import GDPRService

router = APIRouter(prefix="/me", tags=["gdpr"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/export", status_code=202)
@api_limit
async def request_export(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[ExportJobCreated]:
    job = await GDPRService(session).request_export(user.id)
    await session.commit()
    return Envelope(
        data=ExportJobCreated(job_id=job.id), meta=Meta(request_id=_request_id(request))
    )


@router.get("/export/{job_id}")
@api_limit
async def get_export_job(
    request: Request,
    job_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[DataExportJobPublic]:
    job = await GDPRService(session).get_export_job(user.id, job_id)
    return Envelope(
        data=DataExportJobPublic.model_validate(job), meta=Meta(request_id=_request_id(request))
    )


@router.delete("", status_code=202)
@api_limit
async def delete_me(
    request: Request,
    body: DeleteMeRequest,  # noqa: ARG001 -- presence/value of "confirm" is the gate FastAPI enforces
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    await GDPRService(session).delete_me(user)
    await session.commit()
    # T-240, security-relevant: delete_me sets deleted_at, which
    # get_current_user rejects on. Without this the account keeps working for
    # the rest of the cache TTL -- deactivation has to take effect on the
    # *next* request, not a minute later.
    await invalidate_user_cache(redis, user.id)
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.get("/consents")
@api_limit
async def list_consents(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[list[ConsentRecordPublic]]:
    consents = await GDPRService(session).list_consents(user.id)
    data = [ConsentRecordPublic.model_validate(c) for c in consents]
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.post("/consents")
@api_limit
async def record_consent(
    request: Request,
    body: RecordConsentRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[ConsentRecordPublic]:
    record = await GDPRService(session).record_consent(
        user_id=user.id,
        consent_type=body.consent_type,
        granted=body.granted,
        version=body.version,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(
        data=ConsentRecordPublic.model_validate(record), meta=Meta(request_id=_request_id(request))
    )
