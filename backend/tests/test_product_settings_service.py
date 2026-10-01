"""
T-263, the service and key-registry half: validation of well-known keys, the
merge/unset semantics of `apply`, the 60s Redis cache and its invalidation on
write, and the audit event every change leaves.

The HTTP half (403 for a non-superadmin, the 503 the middleware returns) lives
in test_admin_product_settings_router.py and test_maintenance_mode.py.
"""

from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache, cache_keys
from app.modules.audit.models import AuditEvent
from app.modules.product_settings import keys
from app.modules.product_settings.models import SINGLETON_ID, ProductSettings
from app.modules.product_settings.repository import ProductSettingsRepository
from app.modules.product_settings.service import (
    AUDIT_ACTION,
    ProductSettingsService,
    global_banner,
    is_maintenance_mode,
    kill_switch,
    maintenance_message,
    read_settings,
)

_ACTOR = uuid4()


@pytest.fixture(autouse=True)
def _settings_session(product_settings_session: None) -> None:
    """Every test here calls `read_settings` (directly, or through the
    service) without going near the HTTP client, so each one needs the same
    non-DI session seam the middleware does -- see the conftest fixture."""


@pytest.fixture
async def service(session: AsyncSession, redis: Redis) -> ProductSettingsService:
    return ProductSettingsService(session, redis)


# ---- the singleton row -------------------------------------------------------


async def test_migration_seeded_exactly_one_row(session: AsyncSession) -> None:
    rows = (await session.execute(select(ProductSettings))).scalars().all()

    assert [row.id for row in rows] == [SINGLETON_ID]
    assert rows[0].settings == {}


async def test_a_second_row_is_rejected_by_the_database(session: AsyncSession) -> None:
    """The `id = 1` CHECK constraint, not just the application's discipline --
    an operator running a direct INSERT during an incident must not be able to
    create a second, invisible settings row."""
    session.add(ProductSettings(id=2, settings={}))

    with pytest.raises(Exception, match="product_settings_singleton"):
        await session.flush()

    await session.rollback()


# ---- validation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (keys.MAINTENANCE_MODE, "true"),  # the string that is truthy everywhere
        (keys.MAINTENANCE_MODE, 1),
        (keys.MAINTENANCE_MESSAGE, {"text": "back soon"}),
        (keys.SIGNUP_DISABLED, "yes"),
        (keys.GLOBAL_BANNER, "we are down"),
        (keys.GLOBAL_BANNER, {"type": "urgent", "message": "hi"}),
        (keys.GLOBAL_BANNER, {"type": "info", "message": "   "}),
        (keys.GLOBAL_BANNER, {"type": "info", "message": "hi", "dismissible": "no"}),
        (keys.EMERGENCY_KILL_SWITCH, ["billing"]),
        (keys.EMERGENCY_KILL_SWITCH, {"billing": "on"}),
        (keys.EMERGENCY_KILL_SWITCH, {"admin": True}),  # deliberately not killable
        (keys.EMERGENCY_KILL_SWITCH, {"gdpr": True}),  # shares /me with auth
    ],
)
def test_rejects_a_wrong_shaped_value_for_a_well_known_key(key: str, value: object) -> None:
    with pytest.raises(keys.SettingsValidationError):
        keys.validate(key, value)


def test_accepts_an_unknown_key_verbatim() -> None:
    """The raw-JSON editor's whole purpose: a product built on this template
    ships its own switches without a change to the template's code."""
    assert keys.validate("acme_holiday_mode", {"until": "2026-12-26"}) == {"until": "2026-12-26"}


def test_banner_is_rebuilt_rather_than_passed_through() -> None:
    """Extra keys pasted into the raw editor never reach the public /config
    response."""
    validated = keys.validate(
        keys.GLOBAL_BANNER,
        {"type": "warning", "message": "Degraded", "dismissible": False, "onclick": "alert(1)"},
    )

    assert validated == {"type": "warning", "message": "Degraded", "dismissible": False}


def test_banner_dismissible_defaults_to_true() -> None:
    validated = keys.validate(keys.GLOBAL_BANNER, {"type": "info", "message": "Hello"})

    assert validated["dismissible"] is True


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/billing/subscription", "billing"),
        ("/api/v1/billing", "billing"),
        ("/api/v1/billingsomething", None),  # not a segment boundary
        ("/api/v1/files/upload", None),  # switch is off for this module
        ("/api/v1/auth/login", None),
    ],
)
def test_kill_switch_matches_on_a_segment_boundary(path: str, expected: str | None) -> None:
    assert keys.killed_module_for_path({"billing": True, "file_storage": False}, path) == expected


# ---- reads -------------------------------------------------------------------


async def test_read_settings_is_cached_and_busted_on_write(
    session: AsyncSession, redis: Redis, service: ProductSettingsService
) -> None:
    assert await read_settings(redis) == {}
    # Proves the value above came back through Redis rather than the DB every
    # time -- otherwise the next assertion couldn't distinguish "cache works"
    # from "there is no cache".
    assert await redis.get(cache_keys.product_settings_key()) is not None

    # A write behind the service's back: the cache still holds the old answer.
    await ProductSettingsRepository(session).replace({keys.MAINTENANCE_MODE: True}, _ACTOR)
    assert await read_settings(redis) == {}

    # Through the service, the key is busted before `set` returns.
    await service.set(keys.SIGNUP_DISABLED, value=True, actor_id=_ACTOR)
    assert await read_settings(redis) == {
        keys.MAINTENANCE_MODE: True,
        keys.SIGNUP_DISABLED: True,
    }


async def test_read_settings_survives_a_missing_row(session: AsyncSession, redis: Redis) -> None:
    """A database restored from before this migration, or a truncated table:
    every setting reads as its default rather than 500ing the whole API."""
    row = await ProductSettingsRepository(session).get()
    assert row is not None
    await session.delete(row)
    await session.flush()
    await cache.invalidate(redis, cache_keys.product_settings_key())

    settings = await read_settings(redis)

    assert settings == {}
    assert is_maintenance_mode(settings) is False
    assert global_banner(settings) is None
    assert kill_switch(settings) == {}


def test_maintenance_message_falls_back_when_the_operator_left_it_blank() -> None:
    assert maintenance_message({}, fallback="Back soon") == "Back soon"
    blank = {keys.MAINTENANCE_MESSAGE: "   "}
    assert maintenance_message(blank, fallback="Back soon") == "Back soon"
    assert maintenance_message({keys.MAINTENANCE_MESSAGE: "Upgrading"}, fallback="x") == "Upgrading"


def test_a_banner_stored_in_a_shape_the_code_no_longer_understands_reads_as_none() -> None:
    """This row is the one place an operator can put arbitrary JSON, including
    by direct UPDATE during an incident. A stale shape must not become a 500
    on the public, unauthenticated /config endpoint."""
    assert global_banner({keys.GLOBAL_BANNER: {"severity": "high", "text": "old shape"}}) is None


# ---- writes ------------------------------------------------------------------


async def test_apply_merges_and_none_unsets(service: ProductSettingsService) -> None:
    await service.apply({keys.MAINTENANCE_MODE: True, keys.SIGNUP_DISABLED: True}, _ACTOR)
    await service.apply({keys.MAINTENANCE_MODE: None}, _ACTOR)

    stored = await service.all()

    # Unset, not stored as False: the two look identical in the UI and read
    # very differently in an audit diff.
    assert stored == {keys.SIGNUP_DISABLED: True}


async def test_replace_all_removes_absent_keys(service: ProductSettingsService) -> None:
    await service.apply({keys.MAINTENANCE_MODE: True, keys.SIGNUP_DISABLED: True}, _ACTOR)

    await service.replace_all({keys.SIGNUP_DISABLED: False}, _ACTOR)

    assert await service.all() == {keys.SIGNUP_DISABLED: False}


async def test_writes_record_the_actor_and_the_time(
    service: ProductSettingsService, session: AsyncSession
) -> None:
    await service.set(keys.MAINTENANCE_MODE, value=True, actor_id=_ACTOR)

    row = await ProductSettingsRepository(session).get()

    assert row is not None
    assert row.updated_by == _ACTOR
    assert row.updated_at is not None


async def _audit_events(session: AsyncSession) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.action == AUDIT_ACTION).order_by(AuditEvent.ts)
    return list((await session.execute(stmt)).scalars().all())


async def test_every_change_writes_one_elevated_audit_event_with_a_key_level_diff(
    service: ProductSettingsService, session: AsyncSession
) -> None:
    await service.apply(
        {keys.MAINTENANCE_MODE: True, keys.MAINTENANCE_MESSAGE: "Upgrading"}, _ACTOR
    )

    events = await _audit_events(session)

    assert len(events) == 1
    event = events[0]
    assert event.severity == "elevated"
    assert event.actor_id == _ACTOR
    assert event.org_id is None  # global action, no org to attribute it to
    assert event.diff == {
        keys.MAINTENANCE_MODE: {"from": None, "to": True},
        keys.MAINTENANCE_MESSAGE: {"from": None, "to": "Upgrading"},
    }


async def test_the_diff_names_only_what_moved(
    service: ProductSettingsService, session: AsyncSession
) -> None:
    await service.apply({keys.MAINTENANCE_MODE: True, keys.SIGNUP_DISABLED: True}, _ACTOR)

    await service.set(keys.MAINTENANCE_MODE, value=False, actor_id=_ACTOR)

    events = await _audit_events(session)
    assert events[-1].diff == {keys.MAINTENANCE_MODE: {"from": True, "to": False}}


async def test_an_unchanged_submission_is_not_a_change(
    service: ProductSettingsService, session: AsyncSession
) -> None:
    """An operator re-submitting the same form performed no action, and an
    audit trail that says otherwise is one an incident reviewer has to
    filter."""
    await service.set(keys.MAINTENANCE_MODE, value=True, actor_id=_ACTOR)

    await service.set(keys.MAINTENANCE_MODE, value=True, actor_id=_ACTOR)

    assert len(await _audit_events(session)) == 1


async def test_an_invalid_value_is_rejected_before_anything_is_stored(
    service: ProductSettingsService, session: AsyncSession
) -> None:
    with pytest.raises(keys.SettingsValidationError):
        await service.apply({keys.MAINTENANCE_MODE: True, keys.SIGNUP_DISABLED: "yes"}, _ACTOR)

    # Not partially applied: the valid key in the same payload didn't land
    # either, because validation runs over the whole merge before the write.
    assert await service.all() == {}
    assert await _audit_events(session) == []


async def test_get_reads_one_key_through_the_cache(service: ProductSettingsService) -> None:
    await service.set(keys.MAINTENANCE_MESSAGE, "Upgrading the database", _ACTOR)

    assert await service.get(keys.MAINTENANCE_MESSAGE) == "Upgrading the database"
    assert await service.get("never_set") is None


async def test_a_write_recreates_the_row_when_it_is_missing(
    service: ProductSettingsService, session: AsyncSession, redis: Redis
) -> None:
    """The upsert half of the missing-row promise.

    `read_settings` already tolerates a missing row by reporting defaults; this
    is the other side of it -- a database restored from a backup taken before
    T-263's migration must not make every *write* fail with a confusing
    NoResultFound. The operator flipping maintenance mode on such a database is
    doing so because something is already wrong.
    """
    existing = await ProductSettingsRepository(session).get()
    assert existing is not None
    await session.delete(existing)
    await session.flush()
    await cache.invalidate(redis, cache_keys.product_settings_key())

    await service.set(keys.MAINTENANCE_MODE, value=True, actor_id=_ACTOR)

    row = await ProductSettingsRepository(session).get()
    assert row is not None
    assert row.id == SINGLETON_ID
    assert row.settings == {keys.MAINTENANCE_MODE: True}
    assert row.updated_by == _ACTOR
