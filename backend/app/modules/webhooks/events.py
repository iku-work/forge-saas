"""
The event vocabulary (T-320): every event this product can emit, and the
shape of each one's payload.

This registry is doing three jobs at once, and they are the reason it is a
typed table rather than a list of strings:

  1. **Emitting an unregistered event fails.** `WebhookService.emit` looks the
     name up here, so a typo is an exception at the call site rather than a
     subscription nobody ever receives.
  2. **The UI's event-type multi-select renders from it** (T-322), so a new
     event appears in the picker the moment it is registered -- no second
     hardcoded list to forget.
  3. **The docs render from it.** A published event catalogue that is derived
     cannot go stale, which is the failure every hand-written one has.

## Adding an event

Add a member to `EventType`, then an entry to `EVENT_SCHEMAS` describing its
payload. `tests/modules/test_webhook_events.py` fails the build if either
half is missing, and if any registered payload schema is not a pydantic model
this module can actually validate against.

## Why the payloads are deliberately thin

A webhook payload is a **public contract**: once an integration reads
`data.member.email`, that field can never be removed without breaking it.
So each payload carries identifiers and the two or three fields a consumer
genuinely needs to decide what to do, and consumers who need more are
expected to call the API with an API key (T-311) -- which is authenticated,
scoped, rate-limited, and returns *current* state rather than a snapshot of
the moment the event fired.

The envelope around them (`id`, `type`, `created_at`, `org_id`, `data`)
mirrors what Stripe and GitHub send, because a consumer's existing webhook
plumbing already understands that shape.
"""

# No `from __future__ import annotations` here, deliberately: every model
# below is a pydantic one, and pydantic resolves field annotations at class
# creation -- `UUID` and `datetime` have to be real objects at import time,
# not strings ruff would rather move into a TYPE_CHECKING block.
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.schemas import AppBaseModel


class EventType(StrEnum):
    """Every event a product may emit. A closed set: `emit` rejects anything
    that is not a member, and the management API rejects a subscription to
    one."""

    MEMBER_INVITED = "member.invited"
    MEMBER_JOINED = "member.joined"
    MEMBER_REMOVED = "member.removed"
    MEMBER_ROLE_CHANGED = "member.role_changed"
    ORG_UPDATED = "org.updated"
    SUBSCRIPTION_UPDATED = "subscription.updated"
    FILE_UPLOADED = "file.uploaded"
    API_KEY_CREATED = "api_key.created"
    API_KEY_REVOKED = "api_key.revoked"


class MemberPayload(AppBaseModel):
    membership_id: UUID | None = None
    user_id: UUID | None = None
    email: str
    role: str


class OrgPayload(AppBaseModel):
    org_id: UUID
    name: str
    slug: str


class SubscriptionPayload(AppBaseModel):
    subscription_id: UUID
    plan_slug: str
    status: str


class FilePayload(AppBaseModel):
    file_id: UUID
    filename: str
    size_bytes: int


class ApiKeyPayload(AppBaseModel):
    """No `prefix` sibling called `secret`, and no digest: the credential is
    not in the event. A consumer learns that a key was minted, by whom, and
    what it can reach -- which is what a security integration wants -- and
    learns nothing that would let it use the key."""

    api_key_id: UUID
    name: str
    prefix: str
    scopes: list[str]


# Event name -> the pydantic model its `data` object conforms to. Every member
# of EventType has an entry; the test in tests/modules/test_webhook_events.py
# fails the build if one is ever added without.
EVENT_SCHEMAS: dict[EventType, type[BaseModel]] = {
    EventType.MEMBER_INVITED: MemberPayload,
    EventType.MEMBER_JOINED: MemberPayload,
    EventType.MEMBER_REMOVED: MemberPayload,
    EventType.MEMBER_ROLE_CHANGED: MemberPayload,
    EventType.ORG_UPDATED: OrgPayload,
    EventType.SUBSCRIPTION_UPDATED: SubscriptionPayload,
    EventType.FILE_UPLOADED: FilePayload,
    EventType.API_KEY_CREATED: ApiKeyPayload,
    EventType.API_KEY_REVOKED: ApiKeyPayload,
}

# One human sentence per event, for the management UI's checkbox list and for
# the published catalogue. Kept beside the schema rather than in a locale file
# because it describes the *contract*, not the interface -- an integrator
# reading the API docs sees this text, and it is the same text in every
# language the app is translated into.
EVENT_DESCRIPTIONS: dict[EventType, str] = {
    EventType.MEMBER_INVITED: "An invitation was sent to join the organization.",
    EventType.MEMBER_JOINED: "A user accepted an invitation and became a member.",
    EventType.MEMBER_REMOVED: "A member was removed from the organization.",
    EventType.MEMBER_ROLE_CHANGED: "A member's role changed.",
    EventType.ORG_UPDATED: "The organization's name or slug changed.",
    EventType.SUBSCRIPTION_UPDATED: "The organization's subscription or plan changed.",
    EventType.FILE_UPLOADED: "A file finished uploading and passed scanning.",
    EventType.API_KEY_CREATED: "An API key was minted for the organization.",
    EventType.API_KEY_REVOKED: "An API key was revoked.",
}

ALL_EVENT_TYPES: tuple[str, ...] = tuple(event.value for event in EventType)


class UnknownEventTypeError(ValueError):
    """Raised when a name is not in the registry. A `ValueError` rather than a
    `DomainError` because both callers want to translate it differently: the
    service turns a *subscription* to an unknown event into a 422 naming the
    value, while `emit` lets it escape as a programming error, since only the
    product's own code emits."""


class WebhookEventEnvelope(BaseModel):
    """What a consumer actually receives, before it is serialised.

    `model_config` forbids extras so a payload that has drifted from its
    schema is caught here rather than shipped to every subscriber -- the one
    place in this module where being strict costs nothing, since the producer
    is always this codebase.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    type: str
    created_at: datetime
    org_id: UUID
    data: dict[str, Any]


def parse_event_type(value: str) -> EventType:
    """`value` as a member of the vocabulary, or `UnknownEventTypeError`."""
    try:
        return EventType(value)
    except ValueError as exc:
        raise UnknownEventTypeError(value) from exc


def validate_payload(event_type: EventType, payload: BaseModel) -> dict[str, Any]:
    """The payload as a JSON-ready dict, checked against the registry.

    Takes a *model*, not a dict, so the call site is typed -- the registry
    then confirms the model is the one this event declares. Passing the wrong
    payload model for an event is a programming error and raises `TypeError`
    accordingly; it is not something a request can provoke.
    """
    expected = EVENT_SCHEMAS[event_type]
    if not isinstance(payload, expected):
        raise TypeError(
            f"{event_type.value} expects a {expected.__name__} payload, "
            f"got {type(payload).__name__}"
        )
    return payload.model_dump(mode="json")
