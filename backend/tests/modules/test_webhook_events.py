"""
The event registry (T-320): the half of the public contract that is a table
rather than a route.

Three properties, and each of them is a way the catalogue could quietly
become a lie:

  1. every `EventType` has a payload schema and a description, so the docs
     and the UI's picker cannot render a blank;
  2. emitting something that is not in the registry raises, so a typo at a
     call site is a failure here rather than a subscription nobody ever
     receives;
  3. every registered event is actually emitted somewhere in `app/` -- a
     catalogue entry nothing sends is a promise the published documentation
     cannot keep, and it is exactly the kind of entry that survives a
     refactor because nothing points at it.
"""

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.modules.webhooks.events import (
    ALL_EVENT_TYPES,
    EVENT_DESCRIPTIONS,
    EVENT_SCHEMAS,
    EventType,
    MemberPayload,
    OrgPayload,
    UnknownEventTypeError,
    parse_event_type,
    validate_payload,
)

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_every_event_type_has_a_payload_schema() -> None:
    assert set(EventType) == set(EVENT_SCHEMAS)
    for schema in EVENT_SCHEMAS.values():
        assert issubclass(schema, BaseModel)


def test_every_event_type_has_a_description_the_docs_can_render() -> None:
    assert set(EventType) == set(EVENT_DESCRIPTIONS)
    for description in EVENT_DESCRIPTIONS.values():
        assert description.strip()
        # A sentence, because it is rendered next to a checkbox and in the
        # published catalogue -- not a restatement of the name.
        assert description.endswith(".")


def test_all_event_types_is_the_enum_and_nothing_else() -> None:
    assert tuple(event.value for event in EventType) == ALL_EVENT_TYPES


def test_an_unregistered_name_is_rejected_rather_than_accepted_as_a_string() -> None:
    with pytest.raises(UnknownEventTypeError):
        parse_event_type("member.teleported")


def test_the_wrong_payload_model_for_an_event_is_a_programming_error() -> None:
    """A `TypeError`, deliberately not a DomainError: only this codebase
    emits, so shipping a malformed public contract to every subscriber must
    fail a test rather than turn into a 422 somebody swallows."""
    with pytest.raises(TypeError):
        validate_payload(EventType.ORG_UPDATED, MemberPayload(email="a@b.test", role="member"))


def test_a_valid_payload_serialises_to_json_ready_primitives() -> None:
    from uuid import uuid4  # noqa: PLC0415 -- only this case needs one

    org_id = uuid4()
    data = validate_payload(
        EventType.ORG_UPDATED, OrgPayload(org_id=org_id, name="Acme", slug="acme")
    )

    assert data == {"org_id": str(org_id), "name": "Acme", "slug": "acme"}


def test_every_registered_event_is_emitted_somewhere_in_the_app() -> None:
    """The catalogue is a published contract, so an entry nothing sends is a
    documented event that never arrives.

    A source sweep rather than a runtime check, because "is this emitted"
    cannot be answered by importing anything -- and the failure mode this
    catches is the opposite of a typo: someone adds an event, writes the
    docs, and never wires the call site.
    """
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in APP_DIR.rglob("*.py")
        # The registry itself declares every member; excluding it is what
        # makes this a check on the *call sites*.
        if path.name != "events.py"
    )
    emitted = set(re.findall(r"EventType\.([A-Z_]+)", sources))
    missing = {event.name for event in EventType} - emitted
    assert not missing, (
        f"registered but never emitted: {sorted(missing)}. Either wire a call site "
        f"(app.modules.webhooks.service.emit) or take the entry out of the catalogue -- "
        f"a documented event that never arrives is worse than one that does not exist."
    )
