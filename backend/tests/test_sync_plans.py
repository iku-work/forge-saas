"""Resolving plan definitions against Stripe.

The failure this command exists to prevent is silent. A plan row carrying a
price id Stripe does not recognise makes checkout answer 502 -- visible --
but it also makes _handle_subscription_upserted log
`billing.webhook_unknown_price` and return, which strands a customer who has
paid on the free tier with no error anywhere a person would look. So the
properties worth pinning are mostly about refusing to write.

Stripe is stubbed at `stripe.Price.list`: what is under test is the
resolution and upsert logic, not the SDK.
"""

import pytest
import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plans import PlanDefinition
from app.core.sync_plans import PlanSyncError, sync_plans
from app.modules.billing.models import Plan
from app.modules.billing.repository import PlanRepository

pytestmark = pytest.mark.module_dependent  # billing tables


class _FakeProduct:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRecurring:
    def __init__(self, interval: str, interval_count: int) -> None:
        self.interval = interval
        self.interval_count = interval_count


class _FakePrice:
    def __init__(
        self,
        price_id: str,
        unit_amount: int,
        product: str,
        interval: str = "month",
        interval_count: int = 1,
    ) -> None:
        self.id = price_id
        self.unit_amount = unit_amount
        self.product = _FakeProduct(product)
        self.recurring = _FakeRecurring(interval, interval_count)


class _FakeList:
    def __init__(self, data: list[_FakePrice]) -> None:
        self.data = data


def _stub_stripe(monkeypatch: pytest.MonkeyPatch, catalog: dict[str, _FakePrice]) -> None:
    def _list(**kwargs: object) -> _FakeList:
        lookup_keys = kwargs["lookup_keys"]
        assert isinstance(lookup_keys, list)
        (key,) = lookup_keys
        price = catalog.get(key)
        return _FakeList([price] if price is not None else [])

    monkeypatch.setattr(stripe.Price, "list", _list)


PRO = PlanDefinition(
    slug="pro",
    lookup_key_monthly="pro_monthly",
    lookup_key_yearly="pro_yearly",
    features={"seats": 25, "storage_gb": 50},
)


async def test_writes_the_resolved_ids_and_keeps_entitlements_local(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_stripe(
        monkeypatch,
        {
            "pro_monthly": _FakePrice("price_live_m", 1200, "Plus"),
            "pro_yearly": _FakePrice("price_live_y", 12000, "Plus", interval="year"),
        },
    )

    await sync_plans(session, [PRO])

    plan = await PlanRepository(session).get_by_slug("pro")
    assert plan is not None
    assert plan.stripe_price_id_monthly == "price_live_m"
    assert plan.stripe_price_id_yearly == "price_live_y"
    # Name and amount come from Stripe...
    assert plan.name == "Plus"
    assert plan.price_monthly == 1200
    # ...entitlements never do. Stripe has no notion of `storage_gb`.
    assert plan.features == {"seats": 25, "storage_gb": 50}


async def test_a_missing_lookup_key_writes_nothing_at_all(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-applying would leave precisely the state this prevents: one row
    correct, another carrying an id Stripe will not recognise."""
    _stub_stripe(monkeypatch, {"pro_monthly": _FakePrice("price_live_m", 1200, "Plus")})

    with pytest.raises(PlanSyncError, match="pro_yearly"):
        await sync_plans(session, [PRO])

    assert await PlanRepository(session).get_by_slug("pro") is None


async def test_the_error_names_every_missing_key_not_just_the_first(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixing these means clicking through the Stripe dashboard. Reporting
    one per run turns that into as many round trips as there are plans."""
    _stub_stripe(monkeypatch, {})
    other = PlanDefinition(slug="team", lookup_key_monthly="team_monthly")

    with pytest.raises(PlanSyncError) as excinfo:
        await sync_plans(session, [PRO, other])

    assert "pro_monthly" in str(excinfo.value)
    assert "team_monthly" in str(excinfo.value)


async def test_rerunning_updates_in_place_rather_than_duplicating(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is run on every deploy and after every price change, so it has to
    be idempotent -- and it has to pick up a reprice."""
    _stub_stripe(
        monkeypatch,
        {
            "pro_monthly": _FakePrice("price_v1", 1200, "Plus"),
            "pro_yearly": _FakePrice("price_y", 12000, "Plus", interval="year"),
        },
    )
    await sync_plans(session, [PRO])

    # The amount changed in the dashboard, so Stripe issued a new price and
    # `transfer_lookup_key` moved the key onto it.
    _stub_stripe(
        monkeypatch,
        {
            "pro_monthly": _FakePrice("price_v2", 1500, "Plus Plan"),
            "pro_yearly": _FakePrice("price_y", 12000, "Plus Plan", interval="year"),
        },
    )
    await sync_plans(session, [PRO])

    plans = await PlanRepository(session).list_active()
    assert [p.slug for p in plans] == ["pro"]
    assert plans[0].stripe_price_id_monthly == "price_v2"
    assert plans[0].price_monthly == 1500
    assert plans[0].name == "Plus Plan"


async def test_a_withdrawn_plan_is_deactivated_not_deleted(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subscription rows point at plan.id, and _handle_subscription_upserted
    still has to resolve one for a customer who is mid-term on a plan the
    product has stopped selling."""
    session.add(
        Plan(
            slug="legacy",
            name="Legacy",
            stripe_price_id_monthly="price_legacy",
            stripe_price_id_yearly="",
            price_monthly=900,
            price_yearly=0,
            features={},
        )
    )
    await session.commit()
    _stub_stripe(
        monkeypatch,
        {
            "pro_monthly": _FakePrice("price_live_m", 1200, "Plus"),
            "pro_yearly": _FakePrice("price_live_y", 12000, "Plus", interval="year"),
        },
    )

    await sync_plans(session, [PRO])

    # Deliberately not get_by_slug, which filters on is_active and would
    # report the row as gone.
    legacy = await PlanRepository(session).get_by_slug_including_inactive("legacy")
    assert legacy is not None, "the row must survive -- subscriptions reference it"
    assert legacy.is_active is False
    assert [p.slug for p in await PlanRepository(session).list_active()] == ["pro"]


async def test_a_plan_without_a_yearly_price_is_allowed(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plenty of products bill monthly only."""
    _stub_stripe(monkeypatch, {"team_monthly": _FakePrice("price_t", 800, "Team")})

    await sync_plans(session, [PlanDefinition(slug="team", lookup_key_monthly="team_monthly")])

    plan = await PlanRepository(session).get_by_slug("team")
    assert plan is not None
    assert plan.stripe_price_id_yearly == ""


async def test_restoring_a_withdrawn_plan_reactivates_its_row(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """slug is unique, so a restored plan has to reuse the deactivated row.
    Looking it up with the active-only query finds nothing and inserts a
    second row with the same slug, which the constraint rejects."""
    catalog = {
        "pro_monthly": _FakePrice("price_live_m", 1200, "Plus"),
        "pro_yearly": _FakePrice("price_live_y", 12000, "Plus", interval="year"),
    }
    _stub_stripe(monkeypatch, catalog)
    await sync_plans(session, [PRO])

    _stub_stripe(monkeypatch, {"team_monthly": _FakePrice("price_t", 800, "Team")})
    await sync_plans(session, [PlanDefinition(slug="team", lookup_key_monthly="team_monthly")])
    withdrawn = await PlanRepository(session).get_by_slug_including_inactive("pro")
    assert withdrawn is not None
    assert withdrawn.is_active is False

    _stub_stripe(monkeypatch, catalog)
    await sync_plans(session, [PRO])

    restored = await PlanRepository(session).get_by_slug("pro")
    assert restored is not None
    assert restored.is_active is True
    assert restored.id == withdrawn.id, "must reuse the row, not insert a duplicate slug"


async def test_a_yearly_lookup_key_on_a_monthly_price_is_refused(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mistake this caught in production, before it could charge anyone.

    A price named `pro_yearly` that recurs monthly bills twelve times what
    the customer agreed to, and nothing downstream questions it: the interval
    lives in Stripe, BillingService only picks a column. Stripe will happily
    hold such a price, so this is the only place it can be stopped.
    """
    _stub_stripe(
        monkeypatch,
        {
            "pro_monthly": _FakePrice("price_m", 4900, "Pro"),
            # 300.00 "yearly" -- but recurring every month.
            "pro_yearly": _FakePrice("price_y", 30000, "Pro", interval="month"),
        },
    )

    with pytest.raises(PlanSyncError, match="not every 1 year"):
        await sync_plans(session, [PRO])

    assert await PlanRepository(session).get_by_slug("pro") is None


async def test_a_monthly_price_billed_quarterly_is_refused(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """interval_count matters as much as interval: every 3 months is not
    monthly, and the customer would be billed a third as often as the plan
    claims."""
    _stub_stripe(
        monkeypatch,
        {"team_monthly": _FakePrice("price_q", 800, "Team", interval="month", interval_count=3)},
    )

    with pytest.raises(PlanSyncError, match="not every 1 month"):
        await sync_plans(session, [PlanDefinition(slug="team", lookup_key_monthly="team_monthly")])
