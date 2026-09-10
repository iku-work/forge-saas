"""Resolve product.config's PLANS against Stripe and write the price ids.

    make sync-plans

Run it after editing PLANS, after creating or repricing anything in the
Stripe dashboard, and once per environment -- test-mode and live-mode price
ids differ, while the lookup keys do not, which is the point of keying on
them (see app.core.plans).

Design notes, in the order they matter:

**It fails loudly and writes nothing on a partial resolve.** Every lookup key
is resolved before a single row is touched, and a missing one aborts the
whole run naming what was missing. Half-applying would leave exactly the
state this command exists to prevent: a plan row carrying an id Stripe does
not recognise, which makes checkout 502 and -- far worse -- makes
_handle_subscription_upserted log `billing.webhook_unknown_price` and return,
stranding a paying customer on the free tier with no error anywhere a person
would look.

**Stripe owns name and amount; the repo owns entitlements.** Renaming a
product or changing its price in the dashboard is picked up on the next run
with no code change. `features` can never come from Stripe, which has no
notion of what `storage_gb: 50` means.

**A plan that disappears from PLANS is deactivated, not deleted.** Existing
subscriptions still resolve through it -- `is_active=False` only removes it
from the public price list (PlanRepository.list_active).
"""

import asyncio
import logging
from dataclasses import dataclass

import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.logging import configure_logging
from app.core.module_loader import is_enabled
from app.core.plans import PlanDefinition
from app.modules.billing.models import Plan
from app.modules.billing.repository import PlanRepository

logger = logging.getLogger(__name__)


class PlanSyncError(RuntimeError):
    """Raised with everything that could not be resolved, not just the first."""


@dataclass(frozen=True)
class _ResolvedPrice:
    price_id: str
    unit_amount: int
    product_name: str
    currency: str
    interval: str | None
    interval_count: int


def _cadence(price: _ResolvedPrice) -> str:
    if price.interval is None:
        return "a one-off charge"
    return f"every {price.interval_count} {price.interval}(s)"


def _resolve(lookup_key: str) -> _ResolvedPrice | None:
    prices = stripe.Price.list(
        lookup_keys=[lookup_key], active=True, expand=["data.product"], limit=2
    ).data
    if not prices:
        return None
    if len(prices) > 1:
        # Stripe enforces uniqueness among *active* prices, so this means the
        # account is in a state the API says cannot happen. Refusing beats
        # silently picking one and billing at the wrong rate.
        raise PlanSyncError(f"lookup_key {lookup_key!r} matches more than one active price")
    price = prices[0]
    product = price.product
    recurring = price.recurring
    return _ResolvedPrice(
        price_id=price.id,
        unit_amount=price.unit_amount or 0,
        product_name=getattr(product, "name", None) or str(product),
        currency=price.currency,
        interval=recurring.interval if recurring else None,
        interval_count=recurring.interval_count if recurring else 0,
    )


_Resolved = dict[str, tuple[_ResolvedPrice, _ResolvedPrice | None]]


def _disagreements(
    definition: PlanDefinition, monthly: _ResolvedPrice, yearly: _ResolvedPrice | None
) -> list[str]:
    """Ways a resolved pair can contradict what the definition claims.

    Stripe will hold every one of these happily; nothing downstream would
    question them either, because BillingService only picks a column and the
    frontends only render what the row says. This is the one place they can
    be caught.
    """
    problems: list[str] = []

    # A "yearly" price that actually recurs monthly bills twelve times what
    # the customer agreed to. interval_count matters as much as interval:
    # every 3 months is not monthly.
    for label, price, want in (("monthly", monthly, "month"), ("yearly", yearly, "year")):
        if price is None:
            continue
        if price.interval != want or price.interval_count != 1:
            problems.append(
                f"{definition.slug}: the {label} price ({price.price_id}) is billed "
                f"{_cadence(price)}, not every 1 {want}"
            )

    # One plan, one currency: Plan carries a single `currency` for both
    # amounts, so a EUR monthly beside a USD yearly would misprice whichever
    # one lost.
    if yearly is not None and yearly.currency != monthly.currency:
        problems.append(
            f"{definition.slug}: monthly is {monthly.currency.upper()} but yearly is "
            f"{yearly.currency.upper()} -- a plan carries one currency for both"
        )

    return problems


def _resolve_all(definitions: list[PlanDefinition]) -> _Resolved:
    resolved: _Resolved = {}
    missing: list[str] = []
    mismatched: list[str] = []

    for definition in definitions:
        monthly = _resolve(definition.lookup_key_monthly)
        if monthly is None:
            missing.append(
                f"{definition.slug}: monthly lookup_key {definition.lookup_key_monthly!r}"
            )
            continue

        yearly = None
        if definition.lookup_key_yearly is not None:
            yearly = _resolve(definition.lookup_key_yearly)
            if yearly is None:
                missing.append(
                    f"{definition.slug}: yearly lookup_key {definition.lookup_key_yearly!r}"
                )
                continue

        mismatched.extend(_disagreements(definition, monthly, yearly))

        resolved[definition.slug] = (monthly, yearly)

    if mismatched:
        raise PlanSyncError(
            "These Stripe prices do not recur at the cadence their lookup key claims:\n  "
            + "\n  ".join(mismatched)
            + "\n\nA price's interval cannot be edited once it exists. Create a replacement "
            "at the right cadence and move the lookup key onto it "
            "(`transfer_lookup_key=True`, or clear the key on the old price and set it on the "
            "new one in the dashboard). Nothing was written."
        )

    if missing:
        raise PlanSyncError(
            "No active Stripe price carries these lookup keys:\n  "
            + "\n  ".join(missing)
            + "\n\nSet the lookup key on the price in the Stripe dashboard "
            "(Product -> the price -> Edit -> Advanced -> Lookup key), or create the price "
            "with one. Check you are pointed at the right mode: test-mode and live-mode "
            "prices are separate objects and STRIPE_SECRET_KEY decides which you see. "
            "Nothing was written."
        )
    return resolved


def _blank_plan(slug: str) -> Plan:
    """A row whose every field is overwritten immediately below -- the
    constructor just has to satisfy the model's required arguments."""
    return Plan(
        slug=slug,
        name="",
        stripe_price_id_monthly="",
        stripe_price_id_yearly="",
        price_monthly=0,
        price_yearly=0,
        currency="usd",
        features={},
    )


async def sync_plans(session: AsyncSession, definitions: list[PlanDefinition]) -> list[Plan]:
    """Resolve every definition, then upsert. Returns the plans written."""
    resolved = _resolve_all(definitions)

    plans = PlanRepository(session)
    written: list[Plan] = []
    by_slug = {definition.slug: definition for definition in definitions}

    for slug, (monthly, yearly) in resolved.items():
        definition = by_slug[slug]
        # Including inactive: a plan dropped from PLANS was deactivated
        # rather than deleted, and restoring it must reactivate that row.
        # slug is unique, so the active-only lookup would miss it here and
        # then fail on insert.
        existing = await plans.get_by_slug_including_inactive(slug)
        target = existing or _blank_plan(slug)

        target.name = monthly.product_name
        target.price_monthly = monthly.unit_amount
        target.price_yearly = yearly.unit_amount if yearly else 0
        target.currency = monthly.currency
        target.stripe_price_id_monthly = monthly.price_id
        target.stripe_price_id_yearly = yearly.price_id if yearly else ""
        target.features = dict(definition.features)
        target.usage_meter_event_names = dict(definition.usage_meter_event_names)
        target.is_active = True

        if existing is None:
            session.add(target)
        written.append(target)
        logger.info(
            "plans.synced: slug=%s name=%r currency=%s monthly=%s yearly=%s",
            slug,
            target.name,
            target.currency.upper(),
            monthly.price_id,
            yearly.price_id if yearly else "-",
        )

    # Anything the product no longer sells stops being offered, but keeps its
    # row so existing subscriptions still resolve through it.
    for plan in await plans.list_active():
        if plan.slug not in resolved:
            plan.is_active = False
            logger.info("plans.deactivated: slug=%s (no longer in PLANS)", plan.slug)

    await session.commit()
    return written


async def main() -> None:
    settings = get_settings()
    configure_logging(settings=settings)

    if not is_enabled("billing"):
        logger.error("The billing module is disabled -- there are no plan rows to sync.")
        raise SystemExit(1)
    if not settings.STRIPE_SECRET_KEY:
        logger.error("STRIPE_SECRET_KEY is not set.")
        raise SystemExit(1)

    # Imported here, not at module scope, for the same reason get_modules()
    # does it lazily: a name bound at import time would not see a test's
    # monkeypatch of product.config.
    from product.config import PLANS  # noqa: PLC0415

    if not PLANS:
        logger.warning("PLANS is empty in product/config.py -- nothing to sync.")
        return

    stripe.api_key = settings.STRIPE_SECRET_KEY
    async with async_session_factory() as session:
        try:
            written = await sync_plans(session, list(PLANS))
        except PlanSyncError as exc:
            logger.error("%s", exc)  # noqa: TRY400 — the message is the report, a traceback is noise
            raise SystemExit(1) from exc

    mode = "live" if settings.STRIPE_SECRET_KEY.startswith("sk_live") else "test"
    logger.info("Synced %d plan(s) against Stripe in %s mode.", len(written), mode)


if __name__ == "__main__":
    asyncio.run(main())
