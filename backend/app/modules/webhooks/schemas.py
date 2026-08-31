"""
Request/response schemas for the `outbound_webhooks` module (T-320/T-322).

§0.4, no mass assignment: the request schemas are hand-written lists of the
fields a caller may set, never the models themselves. Nothing here accepts
`org_id`, `secret`, `consecutive_failures`, `disabled_at` or any delivery
field -- a client that sends them is ignored, because there is no field to
bind them to.

The secret is the shape to look at. `WebhookEndpointPublic` has no `secret`
field at all, so no read of an endpoint can ever return one; the value exists
in exactly two response shapes, `WebhookEndpointCreated` and
`WebhookSecretRotated`, each returned once by the one endpoint that mints it.
Same contract as `ApiKeyCreated` (T-310), for the same reason: a credential a
UI can re-fetch is a credential in a react-query cache.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.schemas import AppBaseModel

DeliveryStatus = Literal["pending", "delivered", "failed", "dead"]

# 2048 matches WebhookEndpoint.url's column width. Long, because a consumer's
# URL routinely carries a path and a query string it did not choose.
MAX_URL_LENGTH = 2048


class WebhookEndpointCreateRequest(AppBaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    # `list[str]`, not `list[EventType]`: an unknown event name is rejected by
    # the service with a message naming the offending value, rather than by
    # pydantic with an anonymous enum dump -- exactly the reasoning
    # ApiKeyCreateRequest.scopes documents for scopes.
    event_types: list[str] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=500)


class WebhookEndpointUpdateRequest(AppBaseModel):
    """PATCH semantics: every field optional, and the service writes only the
    keys the caller actually sent.

    `event_types` *is* editable here, unlike `ApiKey.scopes` -- and the
    difference is not an inconsistency. Widening a key's scopes silently gives
    an existing credential more power; subscribing an endpoint to another
    event only sends it more of the product's own data, to a URL its owner
    already proved they control.
    """

    url: str | None = Field(default=None, min_length=1, max_length=MAX_URL_LENGTH)
    event_types: list[str] | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class WebhookEndpointPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    description: str | None
    event_types: list[str]
    enabled: bool
    consecutive_failures: int
    disabled_at: datetime | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    # Not the previous secret -- only *when* it stops working, which is what a
    # UI needs to render "the old secret is valid for another 14 hours".
    secret_previous_expires_at: datetime | None


class WebhookEndpointCreated(AppBaseModel):
    """The one shape that carries a new endpoint's signing secret."""

    endpoint: WebhookEndpointPublic
    secret: str


class WebhookSecretRotated(AppBaseModel):
    """The rotation response: the new secret, and the moment the old one stops
    being accepted. Both matter -- a rotation whose overlap window a consumer
    cannot see is a rotation they have to guess the deadline for."""

    endpoint: WebhookEndpointPublic
    secret: str
    previous_secret_expires_at: datetime


class WebhookDeliveryPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID
    event_type: str
    status: str
    attempt_count: int
    response_code: int | None
    response_body: str | None
    error: str | None
    next_attempt_at: datetime | None
    delivered_at: datetime | None
    delivery_id: UUID
    replay_of_id: UUID | None
    created_at: datetime
    # The payload as it was captured at emit time. Returned in the detail read
    # only -- a log page carrying every payload is a response nobody reads and
    # a query budget nobody meets.
    payload: dict | None = None  # type: ignore[type-arg]


class WebhookEventTypePublic(AppBaseModel):
    """One entry of the event catalogue the UI's multi-select renders from
    (T-322) -- derived from app.modules.webhooks.events, never a second
    hardcoded list."""

    name: str
    description: str
