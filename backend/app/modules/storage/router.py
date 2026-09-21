"""
HTTP layer for the file storage module (T-112): presign/confirm/list/download/
delete. Gated on the usual FILES_READ/FILES_MANAGE permission -- matches
billing/router.py's own pattern of declaring get_current_org directly
alongside require_permission when a route needs the resolved Organization/
Membership themselves, not just a yes/no.

These routes used to carry require_feature("storage_gb") as well, which
denied any org without a Stripe subscription. That made the plan a paywall
in front of file storage, and since apps/web posts an avatar through this
same module (bucket="avatars"), it also put a paywall in front of setting
your profile picture -- for free orgs, and equally for a paying customer
whose card lapsed past the dunning grace period. The dashboard's unfiltered
GET /files then answered 402 on every page load for a user who could not
possibly satisfy it.

The quota is the gate now. StorageService._check_quota already reads
per-plan numbers (max_upload_mb, storage_gb, max_files) and enforces them
at presign, so "how much" subsumes "whether" for a product where storage
supports the thing being sold rather than being it. presign therefore still
resolves the org's plan -- via get_optional_org_subscription, which returns
the same OrgSubscription without the yes/no -- while the four routes that
only ever took it for the gate no longer ask for it at all.

To sell storage as a plan feature later, put require_feature("storage_gb")
back on the routes that should be paid-only. Leaving avatars reachable then
means gating on the bucket, which the model already anticipates
(StoredFile.bucket is documented as the seam for plan-quota counting) but
which no quota code reads yet; a route-level dependency cannot see
`body.bucket`, so that check belongs in the service.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import (
    get_current_org,
    get_redis,
    reject_during_impersonation,
    require_permission,
)
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.auth.permissions import Permission
from app.modules.billing.dependencies import OrgSubscription, get_optional_org_subscription
from app.modules.storage.schemas import (
    DownloadUrlResponse,
    PresignUploadRequest,
    PresignUploadResponse,
    StoredFilePublic,
)
from app.modules.storage.service import StorageService
from app.modules.tenancy.models import Membership, Organization

router = APIRouter(prefix="/files", tags=["files"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.post("/presign", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@api_limit
async def presign_upload(  # noqa: PLR0913 — org_sub on top of the usual permission + session + redis params
    request: Request,
    body: PresignUploadRequest,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on FILES_MANAGE
        Membership, Depends(require_permission(Permission.FILES_MANAGE))
    ],
    org_sub: Annotated[OrgSubscription | None, Depends(get_optional_org_subscription)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[PresignUploadResponse]:
    org, member = org_and_membership
    result = await StorageService(session, org.id, redis).presign_upload(
        member.user_id,
        filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        bucket=body.bucket,
        org_sub=org_sub,
    )
    await session.commit()
    return Envelope(
        data=PresignUploadResponse(**result), meta=Meta(request_id=_request_id(request))
    )


@router.post(
    "/{file_id}/confirm", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@api_limit
async def confirm_upload(  # noqa: PLR0913 — file_id on top of the usual permission + session + redis params
    request: Request,
    file_id: UUID,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on FILES_MANAGE
        Membership, Depends(require_permission(Permission.FILES_MANAGE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[StoredFilePublic]:
    org, member = org_and_membership
    stored_file = await StorageService(session, org.id, redis).confirm_upload(
        member.user_id, file_id
    )
    await session.commit()
    return Envelope(
        data=StoredFilePublic.model_validate(stored_file),
        meta=Meta(request_id=_request_id(request)),
    )


@router.get("")
@api_limit
async def list_files(  # noqa: PLR0913 — cursor pagination (§0.3) needs after+limit+bucket on top of the usual params
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on FILES_READ
        Membership, Depends(require_permission(Permission.FILES_READ))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    # Defaults to the general-purpose bucket rather than to "every bucket".
    #
    # `bucket` is the seam for storing things that are not the user's
    # documents -- avatars today (set by the profile page, which uploads
    # through this same module), exports and thumbnails plausibly later. With
    # no default, GET /files returned all of them, so the Files page listed
    # every profile picture anyone in the org had ever uploaded. Measured on
    # one production org: 3 live avatars against 2 actual files.
    #
    # An allowlist rather than "not avatars", so the next special-purpose
    # bucket is invisible here without anyone having to remember this comment.
    # Asking for one explicitly still works.
    #
    # Quota accounting is deliberately unaffected: total_bytes_for_org and
    # count_files_for_org are separate queries over every bucket, which is
    # right -- an avatar occupies storage whether or not it is listed here.
    bucket: Annotated[str, Query()] = "default",
) -> Envelope[list[StoredFilePublic]]:
    org, _membership = org_and_membership
    files, next_cursor = await StorageService(session, org.id, redis).list_files(
        after, limit, bucket=bucket
    )
    data = [StoredFilePublic.model_validate(f) for f in files]
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=data, meta=meta)


@router.get("/{file_id}/download")
@api_limit
async def download_file(  # noqa: PLR0913 — file_id on top of the usual permission + session + redis params
    request: Request,
    file_id: UUID,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on FILES_READ
        Membership, Depends(require_permission(Permission.FILES_READ))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[DownloadUrlResponse]:
    org, _membership = org_and_membership
    url, expires_at = await StorageService(session, org.id, redis).get_download_url(file_id)
    return Envelope(
        data=DownloadUrlResponse(url=url, expires_at=expires_at),
        meta=Meta(request_id=_request_id(request)),
    )


@router.delete("/{file_id}", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@api_limit
async def delete_file(  # noqa: PLR0913 — file_id on top of the usual permission + session + redis params
    request: Request,
    file_id: UUID,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on FILES_MANAGE
        Membership, Depends(require_permission(Permission.FILES_MANAGE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    org, member = org_and_membership
    await StorageService(session, org.id, redis).delete_file(member.user_id, file_id)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))
