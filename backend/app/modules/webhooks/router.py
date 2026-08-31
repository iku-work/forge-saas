"""
HTTP layer for the `outbound_webhooks` module (T-320/T-322): endpoint CRUD,
the event catalogue, the delivery log, and replay -- all under
/api/v1/webhooks/.

Gated by a session and `require_permission(SETTINGS_MANAGE)`, never by
`require_scope`: nothing here is part of the public API. An API key that could
register a webhook endpoint is an exfiltration primitive -- point it at a URL
you control and the product starts pushing its own events to you -- so the
management surface stays owner/admin-only, exactly like the API-key one
(T-310's router makes the same argument about a key that can mint a key).

Otherwise the house pattern: an `Envelope` on every response, keyset
pagination on both lists, `@api_limit`, `await session.commit()` in the router
rather than the service, and `reject_during_impersonation` on every write --
a support agent inside an impersonation session must not point a customer's
events at a new URL.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import reject_during_impersonation, require_permission
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.auth.permissions import Permission
from app.modules.tenancy.models import Membership

from .events import EVENT_DESCRIPTIONS, EventType
from .models import ROTATION_OVERLAP_HOURS
from .schemas import (
    DeliveryStatus,
    WebhookDeliveryPublic,
    WebhookEndpointCreated,
    WebhookEndpointCreateRequest,
    WebhookEndpointPublic,
    WebhookEndpointUpdateRequest,
    WebhookEventTypePublic,
    WebhookSecretRotated,
)
from .service import WebhookDeliveryService, WebhookEndpointService

# Both read and manage are SETTINGS_MANAGE: seeing where an organization
# pushes its events is settings-level information, not membership-level, and
# the delivery log carries payloads. Owner and admin, never a plain member --
# the same "org-manage permission" T-322 gates its page on.
READ_PERMISSION = Permission.SETTINGS_MANAGE
MANAGE_PERMISSION = Permission.SETTINGS_MANAGE

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.get(
    "/event-types",
    description=(
        "Every event this product can emit, with a one-line description. "
        "Rendered by the management UI's event picker and by the published "
        "documentation, so a newly registered event appears in both without "
        "a second list to update."
    ),
)
@api_limit
async def list_event_types(
    request: Request,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],  # noqa: ARG001 -- gate only
) -> Envelope[list[WebhookEventTypePublic]]:
    return Envelope(
        # Iterating EventType rather than ALL_EVENT_TYPES (which is the tuple
        # of *strings*): the enum member is what indexes the description map,
        # and the order is the declaration order the UI renders in.
        data=[
            WebhookEventTypePublic(name=event.value, description=EVENT_DESCRIPTIONS[event])
            for event in EventType
        ],
        meta=Meta(request_id=_request_id(request)),
    )


@router.post(
    "/endpoints",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
    description=(
        "Registers an endpoint and returns its signing secret **once**. The "
        "secret is stored encrypted and is never returned by any later read; "
        "rotate it if it is lost. The URL is checked against the outbound "
        "guard both here and again immediately before every delivery."
    ),
)
@api_limit
async def create_endpoint(
    request: Request,
    body: WebhookEndpointCreateRequest,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebhookEndpointCreated]:
    service = WebhookEndpointService(session, membership.org_id)
    endpoint, secret = await service.create_endpoint(membership.user_id, body)
    await session.commit()
    return Envelope(
        data=WebhookEndpointCreated(
            endpoint=WebhookEndpointPublic.model_validate(endpoint), secret=secret
        ),
        meta=Meta(request_id=_request_id(request)),
    )


@router.get("/endpoints")
@api_limit
async def list_endpoints(
    request: Request,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[list[WebhookEndpointPublic]]:
    endpoints, next_cursor = await WebhookEndpointService(
        session, membership.org_id
    ).list_endpoints(after, limit)
    return Envelope(
        data=[WebhookEndpointPublic.model_validate(endpoint) for endpoint in endpoints],
        meta=Meta(
            request_id=_request_id(request),
            next_cursor=str(next_cursor) if next_cursor is not None else None,
        ),
    )


@router.get("/endpoints/{endpoint_id}")
@api_limit
async def get_endpoint(
    request: Request,
    endpoint_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebhookEndpointPublic]:
    endpoint = await WebhookEndpointService(session, membership.org_id).get_endpoint(endpoint_id)
    return Envelope(
        data=WebhookEndpointPublic.model_validate(endpoint),
        meta=Meta(request_id=_request_id(request)),
    )


@router.patch(
    "/endpoints/{endpoint_id}",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
)
@api_limit
async def update_endpoint(
    request: Request,
    endpoint_id: UUID,
    body: WebhookEndpointUpdateRequest,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebhookEndpointPublic]:
    endpoint = await WebhookEndpointService(session, membership.org_id).update_endpoint(
        membership.user_id, endpoint_id, body
    )
    await session.commit()
    return Envelope(
        data=WebhookEndpointPublic.model_validate(endpoint),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post(
    "/endpoints/{endpoint_id}/rotate-secret",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
    description=(
        "Issues a new signing secret and returns it once. The previous secret "
        f"keeps working for {ROTATION_OVERLAP_HOURS} hours -- every request "
        "in that window is signed with both, so a consumer can deploy a "
        "verifier that accepts either and switch over without downtime."
    ),
)
@api_limit
async def rotate_secret(
    request: Request,
    endpoint_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebhookSecretRotated]:
    service = WebhookEndpointService(session, membership.org_id)
    endpoint, secret = await service.rotate_secret(membership.user_id, endpoint_id)
    await session.commit()
    # Never None right after a rotation -- the service has just set it -- but
    # the column is nullable, so the assertion belongs here rather than in the
    # response model, where it would weaken the type for every other reader.
    assert endpoint.secret_previous_expires_at is not None  # noqa: S101
    return Envelope(
        data=WebhookSecretRotated(
            endpoint=WebhookEndpointPublic.model_validate(endpoint),
            secret=secret,
            previous_secret_expires_at=endpoint.secret_previous_expires_at,
        ),
        meta=Meta(request_id=_request_id(request)),
    )


@router.delete(
    "/endpoints/{endpoint_id}",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
)
@api_limit
async def delete_endpoint(
    request: Request,
    endpoint_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await WebhookEndpointService(session, membership.org_id).delete_endpoint(
        membership.user_id, endpoint_id
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.get(
    "/endpoints/{endpoint_id}/deliveries",
    description=(
        "One endpoint's delivery log, newest first, paginated by keyset "
        "(`after` carries the previous page's last id). Payloads are omitted "
        "here and returned by the single-delivery read."
    ),
)
@api_limit
async def list_deliveries(  # noqa: PLR0913 -- the page's three query params on top of the usual four
    request: Request,
    endpoint_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[DeliveryStatus | None, Query()] = None,
) -> Envelope[list[WebhookDeliveryPublic]]:
    deliveries, next_cursor = await WebhookDeliveryService(
        session, membership.org_id
    ).list_deliveries(endpoint_id, after, limit, status)
    return Envelope(
        # model_validate would carry `payload` too -- excluded here rather
        # than dropped from the schema, because the detail read below returns
        # the same model *with* it. A log page carrying every payload is a
        # response nobody reads and a query budget nobody meets.
        data=[
            WebhookDeliveryPublic.model_validate(delivery).model_copy(update={"payload": None})
            for delivery in deliveries
        ],
        meta=Meta(
            request_id=_request_id(request),
            next_cursor=str(next_cursor) if next_cursor is not None else None,
        ),
    )


@router.get("/deliveries/{delivery_id}")
@api_limit
async def get_delivery(
    request: Request,
    delivery_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebhookDeliveryPublic]:
    delivery = await WebhookDeliveryService(session, membership.org_id).get_delivery(delivery_id)
    return Envelope(
        data=WebhookDeliveryPublic.model_validate(delivery),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post(
    "/deliveries/{delivery_id}/replay",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
    description=(
        "Sends the stored payload again as a **new** delivery, with a new "
        "`X-Forge-Delivery` id. The original row is never modified -- the log "
        "is an audit trail, so 'we replayed it and now it says delivered' is "
        "a sentence this API cannot produce."
    ),
)
@api_limit
async def replay_delivery(
    request: Request,
    delivery_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebhookDeliveryPublic]:
    replayed = await WebhookDeliveryService(session, membership.org_id).replay(
        membership.user_id, delivery_id
    )
    await session.commit()
    return Envelope(
        data=WebhookDeliveryPublic.model_validate(replayed),
        meta=Meta(request_id=_request_id(request)),
    )
