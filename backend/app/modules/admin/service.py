"""
AdminFeatureFlagService (T-122): superadmin support/beta-testing overrides on
top of T-120's FeatureFlagService -- lets a superadmin flip a flag for one org
without touching that org's plan. Every mutation writes an AuditEvent
(§0.1: cross-module audit writes go through AuditService, never
AuditRepository directly) and reuses FeatureFlagService.set_override /
delete_override, which already invalidate the flag cache themselves (T-120).

AdminDirectoryService and AdminBillingOverrideService (T-141) are the
read-mostly org/user directory and the plan-override/trial/comp-credit
support-ops writes. Both reach directly into other modules' repositories
(SubscriptionRepository, PlanRepository, SessionRepository, ...) rather than
going through each module's own service -- same justification as above and
as AdminOrgRepository's docstring: the admin surface is a cross-tenant
aggregate view no single module's service was designed to provide.
"""

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import stripe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.audit_context import reset_impersonator_id, set_impersonator_id
from app.core.config import get_settings
from app.core.dependencies import invalidate_user_cache
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.module_loader import is_enabled
from app.core.repository import DEFAULT_PAGE_SIZE
from app.core.token_blocklist import block_jti
from app.core.urls import frontend_url
from app.models._mixins import utcnow
from app.modules.admin.impersonation_handoff import (
    HandoffRecord,
    generate_exchange_token,
    get_session_reason,
    handoff_expiry,
    peek_handoff,
    store_handoff,
    store_session_reason,
    take_handoff,
)
from app.modules.admin.repository import AdminOrgRepository, AdminStatsRepository
from app.modules.admin.schemas import (
    AdminAuditEntry,
    AdminAuditFeedEntry,
    AdminAuditSummary,
    AdminDashboardStats,
    AdminFeatureFlagState,
    AdminMembershipSummary,
    AdminOrgDetail,
    AdminOrgSummary,
    AdminPaymentFailureEntry,
    AdminSessionSummary,
    AdminSubscriptionDetail,
    AdminUserDetail,
    AdminUserSummary,
    ImpersonationChannel,
)
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.repository import SessionRepository, TOTPSecretRepository, UserRepository
from app.modules.auth.utils import create_access_token, verify_access_token
from app.modules.billing.models import Subscription
from app.modules.billing.repository import PlanRepository, SubscriptionRepository
from app.modules.feature_flags.repository import OrgFeatureFlagRepository
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.storage.repository import StoredFileRepository
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

# T-142: hard cap on impersonation-token lifetime, enforced twice --
# AdminImpersonateRequest.duration_minutes (schema, le=60) rejects an
# over-long request before it reaches here, and this constant is what
# start() actually mints the token's own exp with (belt-and-suspenders: a
# future caller that bypasses the schema still can't mint a longer-lived
# token). Also used as the blocklist TTL in end() -- an ended impersonation
# jti never needs to stay blocked longer than the token could have lived.
MAX_IMPERSONATION_MINUTES = 60

_END_ACTIONS: dict[str, str] = {
    "web": "admin.impersonate.end",
    "mobile": "impersonation.mobile_ended",
}

# The path half of the handoff deep link. The scheme comes from settings
# (MOBILE_DEEP_LINK_SCHEME) so a product that renames the app doesn't have to
# find this string; the host is the mobile route that handles it
# (apps/mobile/app/impersonate.tsx), and the two have to be changed together.
_DEEP_LINK_HOST = "impersonate"


def _deep_link(exchange_token: str, user_email: str) -> str:
    # urlencode, not an f-string: an email address is allowed to contain "+"
    # and "&", either of which would silently truncate or corrupt the query
    # string the phone parses.
    query = urlencode({"token": exchange_token, "user_email": user_email})
    return f"{get_settings().MOBILE_DEEP_LINK_SCHEME}://{_DEEP_LINK_HOST}?{query}"


# Placeholder stripe_subscription_id for a Subscription row an admin
# force-assigned without a real Stripe subscription behind it (§T-141's
# "free_override" case) -- Subscription.stripe_subscription_id is NOT NULL +
# unique, so a comp'd org still needs a value here, just not a real Stripe
# one. extend_trial checks this prefix to know whether to call Stripe at all.
ADMIN_OVERRIDE_STRIPE_ID_PREFIX = "admin_override_"


class AdminFeatureFlagService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._flags = FeatureFlagService(session, redis)
        self._audit = AuditService(session)

    async def resolved_flags(self, org: Organization) -> dict[str, AdminFeatureFlagState]:
        resolved = await self._flags.all_enabled(org)
        overrides = await OrgFeatureFlagRepository(self._session, org.id).get_overrides()
        names = set(resolved) | set(overrides)
        return {
            name: AdminFeatureFlagState(
                resolved=resolved.get(name, False), override=overrides.get(name)
            )
            for name in names
        }

    async def set_override(  # noqa: PLR0913 -- mirrors AuditService.log's own field set
        self,
        *,
        org: Organization,
        flag: str,
        enabled: bool,
        actor_id: UUID,
        ip: str | None,
        ua: str | None,
    ) -> None:
        await self._flags.set_override(org.id, flag, enabled)
        await self._audit.log(
            action="admin.feature_flag.set",
            resource_type="feature_flag",
            resource_id=flag,
            org_id=org.id,
            actor_id=actor_id,
            diff={"flag": flag, "enabled": enabled},
            ip=ip,
            ua=ua,
        )

    async def delete_override(
        self,
        *,
        org: Organization,
        flag: str,
        actor_id: UUID,
        ip: str | None,
        ua: str | None,
    ) -> None:
        await self._flags.delete_override(org.id, flag)
        await self._audit.log(
            action="admin.feature_flag.set",
            resource_type="feature_flag",
            resource_id=flag,
            org_id=org.id,
            actor_id=actor_id,
            diff={"flag": flag, "enabled": None},
            ip=ip,
            ua=ua,
        )


class AdminDirectoryService:
    """Read-mostly org/user directory for support ops (T-141)."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        self._org_directory = AdminOrgRepository(session)
        self._orgs = OrganizationRepository(session)
        self._memberships = MembershipRepository(session)
        self._plans = PlanRepository(session)
        self._subscriptions = SubscriptionRepository(session)
        self._audit = AuditRepository(session)
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)

    async def list_orgs(
        self,
        after: UUID | None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        q: str | None = None,
        plan_slug: str | None = None,
        status: str | None = None,
    ) -> tuple[list[AdminOrgSummary], UUID | None]:
        rows, next_cursor = await self._org_directory.list_orgs(
            after, limit, q=q, plan_slug=plan_slug, status=status
        )
        summaries = [
            AdminOrgSummary(
                id=org.id,
                name=org.name,
                slug=org.slug,
                plan_slug=row_plan_slug,
                subscription_status=row_status,
                created_at=org.created_at,
            )
            for org, row_plan_slug, row_status in rows
        ]
        return summaries, next_cursor

    async def get_org_detail(self, org: Organization) -> AdminOrgDetail:
        member_count = await self._memberships.count_by_org(org.id)

        file_count: int | None = None
        if is_enabled("file_storage"):
            file_count = await StoredFileRepository(self._session, org.id).count_files_for_org()

        recent_events, _ = await self._audit.list_cursor(org.id, limit=1)
        last_activity_at = recent_events[0].ts if recent_events else None

        subscription_detail: AdminSubscriptionDetail | None = None
        if is_enabled("billing"):
            subscription = await self._subscriptions.get_by_org_id(org.id)
            if subscription is not None:
                plan = await self._plans.get_by_id(subscription.plan_id)
                subscription_detail = AdminSubscriptionDetail(
                    plan_slug=plan.slug if plan is not None else "unknown",
                    status=subscription.status,
                    billing_interval=subscription.billing_interval,
                    free_override=subscription.free_override,
                    trial_end=subscription.trial_end,
                    current_period_end=subscription.current_period_end,
                    override_expires_at=subscription.override_expires_at,
                )

        return AdminOrgDetail(
            id=org.id,
            name=org.name,
            slug=org.slug,
            created_at=org.created_at,
            member_count=member_count,
            file_count=file_count,
            last_activity_at=last_activity_at,
            subscription=subscription_detail,
        )

    async def list_users(
        self,
        after: UUID | None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        q: str | None = None,
        active: bool | None = None,
    ) -> tuple[list[AdminUserSummary], UUID | None]:
        users, next_cursor = await self._users.list_cursor(after, limit, q=q, active=active)
        summaries = [
            AdminUserSummary(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                is_active=u.is_active,
                created_at=u.created_at,
            )
            for u in users
        ]
        return summaries, next_cursor

    async def get_user_detail(self, user: User) -> AdminUserDetail:
        memberships = await self._memberships.list_for_user(user.id)
        # T-243: one `IN` query for the whole membership list, not one
        # get_by_id per row -- a support-ops user on twenty orgs used to cost
        # twenty round trips here. Soft-deleted orgs are still dropped
        # (list_by_ids filters deleted_at exactly as get_by_id did).
        orgs = {
            org.id: org for org in await self._orgs.list_by_ids([m.org_id for m in memberships])
        }
        membership_summaries = []
        for membership in memberships:
            org = orgs.get(membership.org_id)
            if org is None:
                continue
            membership_summaries.append(
                AdminMembershipSummary(
                    org_id=org.id, org_name=org.name, org_slug=org.slug, role=membership.role
                )
            )

        active_sessions = await self._sessions.list_active_for_user(user.id)
        session_summaries = [
            AdminSessionSummary(
                id=s.id,
                device_label=s.device_label,
                ip_address=s.ip_address,
                last_seen_at=s.last_seen_at,
                expires_at=s.expires_at,
            )
            for s in active_sessions
        ]

        total_count = await self._audit.count_by_actor(user.id)
        recent = await self._audit.list_recent_by_actor(user.id, limit=10)
        audit_summary = AdminAuditSummary(
            total_count=total_count,
            recent_actions=[
                AdminAuditEntry(
                    action=e.action,
                    resource_type=e.resource_type,
                    resource_id=e.resource_id,
                    ts=e.ts,
                )
                for e in recent
            ],
        )

        return AdminUserDetail(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superadmin=user.is_superadmin,
            created_at=user.created_at,
            memberships=membership_summaries,
            sessions=session_summaries,
            audit_summary=audit_summary,
        )

    async def list_audit_feed(  # noqa: PLR0913 -- cursor pagination (§0.3) plus T-143's four filters
        self,
        after: UUID | None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        actor_id: UUID | None = None,
        action: str | None = None,
        org_id: UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[list[AdminAuditFeedEntry], UUID | None]:
        events, next_cursor = await self._audit.list_global_cursor(
            after, limit, actor_id=actor_id, action=action, org_id=org_id, since=since, until=until
        )
        # T-243: two batch lookups for the whole page, not one get_by_id per
        # distinct actor/org. The previous per-page memo dict only helped when
        # a page repeated the same actor -- an admin feed of 100 events from
        # 100 different people cost 200 extra round trips, and the cost grew
        # with the page size, which is the definition of the N+1 this task's
        # constant-query test exists to catch (tests/test_query_budgets.py).
        user_emails = {
            u.id: u.email
            for u in await self._users.list_by_ids([e.actor_id for e in events if e.actor_id])
        }
        org_names = {
            o.id: o.name
            for o in await self._orgs.list_by_ids([e.org_id for e in events if e.org_id])
        }
        entries: list[AdminAuditFeedEntry] = []
        for event in events:
            # A missing id (soft-deleted user/org, both filtered out by
            # list_by_ids) reads as None, exactly as the get_by_id-returns-
            # None branch did before.
            actor_email = user_emails.get(event.actor_id) if event.actor_id else None
            org_name = org_names.get(event.org_id) if event.org_id else None

            entries.append(
                AdminAuditFeedEntry(
                    id=event.id,
                    ts=event.ts,
                    action=event.action,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    severity=event.severity,
                    actor_id=event.actor_id,
                    actor_email=actor_email,
                    org_id=event.org_id,
                    org_name=org_name,
                    impersonator_id=event.impersonator_id,
                )
            )
        return entries, next_cursor


class AdminDashboardService:
    """Aggregate counts + recent activity for the admin dashboard (T-143)."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:  # noqa: ARG002 -- redis kept for signature symmetry with the module's other admin services, unused here (no cached reads on this path)
        self._stats = AdminStatsRepository(session)
        self._users = UserRepository(session)
        self._orgs = OrganizationRepository(session)
        self._audit = AuditRepository(session)

    async def get_stats(self) -> AdminDashboardStats:
        org_count = await self._stats.count_orgs()
        user_count = await self._stats.count_users()
        mrr_cents = await self._stats.sum_mrr_cents() if is_enabled("billing") else None

        recent_users, _ = await self._users.list_cursor(None, limit=5)
        recent_signups = [
            AdminUserSummary(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                is_active=u.is_active,
                created_at=u.created_at,
            )
            for u in recent_users
        ]

        recent_payment_failures: list[AdminPaymentFailureEntry] = []
        if is_enabled("billing"):
            events = await self._audit.list_recent_by_action("billing.payment_failed", limit=5)
            # T-243: one batch lookup, same as the audit feed's. The limit=5
            # cap kept the old per-event get_by_id cheap, but it was still a
            # query per row -- and a cap is not a fix, it is a smaller N.
            orgs = {
                org.id: org
                for org in await self._orgs.list_by_ids([e.org_id for e in events if e.org_id])
            }
            for event in events:
                if event.org_id is None:
                    continue
                org = orgs.get(event.org_id)
                if org is None:
                    continue
                recent_payment_failures.append(
                    AdminPaymentFailureEntry(
                        org_id=org.id,
                        org_name=org.name,
                        subscription_id=event.resource_id,
                        ts=event.ts,
                    )
                )

        return AdminDashboardStats(
            org_count=org_count,
            user_count=user_count,
            mrr_cents=mrr_cents,
            recent_signups=recent_signups,
            recent_payment_failures=recent_payment_failures,
        )


class AdminBillingOverrideService:
    """Force-assign plan / extend trial / comp-credit support-ops writes (T-141)."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._plans = PlanRepository(session)
        self._subscriptions = SubscriptionRepository(session)
        self._audit = AuditService(session)
        self._flags = FeatureFlagService(session, redis)
        stripe.api_key = get_settings().STRIPE_SECRET_KEY

    async def set_plan(  # noqa: PLR0913 -- org+4 request fields+actor+ip+ua, all required for the audit diff
        self,
        *,
        org: Organization,
        plan_slug: str,
        billing_interval: str | None,
        free_override: bool,
        expires_at: datetime | None,
        actor_id: UUID,
        ip: str | None,
        ua: str | None,
    ) -> Subscription:
        plan = await self._plans.get_by_slug(plan_slug)
        if plan is None:
            raise NotFoundError(
                "errors.plan_not_found",
                fallback=f"Plan '{plan_slug}' not found",
                params={"plan_slug": plan_slug},
            )

        existing = await self._subscriptions.get_by_org_id(org.id)
        before: dict[str, Any] | None = None
        if existing is not None:
            old_plan = await self._plans.get_by_id(existing.plan_id)
            before = {
                "plan_slug": old_plan.slug if old_plan is not None else None,
                "billing_interval": existing.billing_interval,
                "free_override": existing.free_override,
            }
            existing.plan_id = plan.id
            existing.billing_interval = billing_interval
            existing.free_override = free_override
            existing.override_expires_at = expires_at
            await self._session.flush()
            subscription = existing
        else:
            now = utcnow()
            subscription = Subscription(
                org_id=org.id,
                plan_id=plan.id,
                stripe_subscription_id=f"{ADMIN_OVERRIDE_STRIPE_ID_PREFIX}{uuid4()}",
                status="active",
                current_period_start=now,
                current_period_end=expires_at or now + timedelta(days=36500),
                billing_interval=billing_interval,
                free_override=free_override,
                override_expires_at=expires_at,
            )
            self._session.add(subscription)
            await self._session.flush()

        await self._flags.invalidate(org.id)
        await self._audit.log(
            action="admin.org.plan_override",
            resource_type="subscription",
            resource_id=str(subscription.id),
            org_id=org.id,
            actor_id=actor_id,
            diff={
                "before": before,
                "after": {
                    "plan_slug": plan_slug,
                    "billing_interval": billing_interval,
                    "free_override": free_override,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
            },
            ip=ip,
            ua=ua,
            severity="elevated",
        )
        return subscription

    async def extend_trial(
        self, *, org: Organization, days: int, actor_id: UUID, ip: str | None, ua: str | None
    ) -> Subscription:
        subscription = await self._subscriptions.get_by_org_id(org.id)
        if subscription is None:
            raise NotFoundError(
                "errors.subscription_not_found",
                fallback="Organization has no subscription to extend",
            )

        old_trial_end = subscription.trial_end
        new_trial_end = (subscription.trial_end or utcnow()) + timedelta(days=days)
        # T-240: the subscription row is part of the org's cached entitlement
        # payload, so a manual extension has to bust it like set_plan does.

        if not subscription.stripe_subscription_id.startswith(ADMIN_OVERRIDE_STRIPE_ID_PREFIX):
            # Real Stripe subscription: extend it there too, so a later
            # customer.subscription.updated webhook reports the same
            # trial_end back instead of clobbering this manual extension
            # (§T-141 "webhook reconciliation respects manual extensions").
            stripe.Subscription.modify(
                subscription.stripe_subscription_id,
                trial_end=int(new_trial_end.timestamp()),
                proration_behavior="none",
            )
        subscription.trial_end = new_trial_end
        await self._session.flush()
        await self._flags.invalidate(org.id)

        await self._audit.log(
            action="admin.org.extend_trial",
            resource_type="subscription",
            resource_id=str(subscription.id),
            org_id=org.id,
            actor_id=actor_id,
            diff={
                "days": days,
                "old_trial_end": old_trial_end.isoformat() if old_trial_end else None,
                "new_trial_end": new_trial_end.isoformat(),
            },
            ip=ip,
            ua=ua,
            severity="elevated",
        )
        return subscription

    async def issue_comp_credit(  # noqa: PLR0913 -- org+amount+reason+actor+ip+ua, all required for the audit diff
        self,
        *,
        org: Organization,
        amount_cents: int,
        reason: str,
        actor_id: UUID,
        ip: str | None,
        ua: str | None,
    ) -> str:
        # Applying a discount to a bare Customer (rather than a specific
        # Subscription) was removed from Stripe's Customer-update API in
        # favor of the Discounts API -- there's nothing to redeem a coupon
        # against without a real Stripe subscription behind this org, so
        # this requires one (a free_override placeholder Subscription,
        # ADMIN_OVERRIDE_STRIPE_ID_PREFIX, has no real Stripe object either).
        subscription = await self._subscriptions.get_by_org_id(org.id)
        if subscription is None or subscription.stripe_subscription_id.startswith(
            ADMIN_OVERRIDE_STRIPE_ID_PREFIX
        ):
            raise NotFoundError(
                "errors.no_billable_subscription",
                fallback="Organization has no billable Stripe subscription to credit",
            )

        coupon = stripe.Coupon.create(
            # T-141 support tool, single-currency assumption: Plan.price_* are
            # already bare cents with no currency column (app.modules.billing.models),
            # so there's no per-org currency to read here either.
            amount_off=amount_cents,
            currency="usd",
            duration="once",
            name=f"Support credit: {reason}"[:40],
        )
        stripe.Subscription.modify(
            subscription.stripe_subscription_id, discounts=[{"coupon": coupon.id}]
        )

        await self._audit.log(
            action="admin.org.comp_credit",
            resource_type="organization",
            resource_id=str(org.id),
            org_id=org.id,
            actor_id=actor_id,
            diff={"amount_cents": amount_cents, "reason": reason, "coupon_id": coupon.id},
            ip=ip,
            ua=ua,
            severity="elevated",
        )
        return coupon.id


class AdminImpersonationService:
    """Support-ops user impersonation (T-142): mints a scoped, non-refreshable
    access token for `target` carrying an `impersonator_id` claim naming the
    real superadmin behind the session. No Session row / refresh cookie is
    ever created for it -- the spec's "cannot be refreshed" requirement falls
    out for free, since app.modules.auth.router.refresh only ever reads a
    refresh-token cookie/body field, never the bearer token, and this flow
    never sets one. That also means a superadmin's own real refresh cookie
    is left untouched while impersonating, so ending impersonation can
    silently fall back to it instead of forcing a fresh login.

    Every audit event written for the rest of an impersonated request (by
    any module) is stamped with impersonator_id automatically -- see
    app.core.audit_context and app.middleware.impersonation, which decode
    the claim straight off the bearer token before routing.
    """

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._redis = redis
        self._audit = AuditService(session)

    async def start(  # noqa: PLR0913 -- superadmin+target+reason+duration+ip+ua, all required for the audit diff
        self,
        *,
        superadmin: User,
        target: User,
        reason: str,
        duration_minutes: int,
        ip: str | None,
        ua: str | None,
    ) -> tuple[str, datetime]:
        # ADR-0009: with DATA_SENSITIVITY=high, support sees what the user shows it,
        # not everything their account can open.
        if get_settings().DATA_SENSITIVITY == "high":
            raise PermissionDeniedError(
                "errors.impersonation_disabled_sensitive_data",
                fallback="Impersonation is turned off: this product holds sensitive data",
            )
        if target.is_superadmin:
            raise PermissionDeniedError(
                "errors.cannot_impersonate_superadmin",
                fallback="Cannot impersonate another superadmin",
            )
        minutes = min(duration_minutes, MAX_IMPERSONATION_MINUTES)
        access_token = create_access_token(
            target.id, None, (), impersonator_id=superadmin.id, expire_minutes=minutes
        )
        # T-261: the reason is kept server-side, keyed by this token's jti, so
        # the mobile confirmation screen can show *why* a session it is about
        # to adopt exists. Deliberately not a JWT claim -- claims are readable
        # by anyone holding the token, and a support reason ("user reports
        # card declined, ticket 8812") is not something to hand to whoever
        # ends up with it. Expires with the token.
        await store_session_reason(
            self._redis, verify_access_token(access_token).jti, reason, minutes * 60
        )
        await self._audit.log(
            action="admin.impersonate.start",
            resource_type="user",
            resource_id=str(target.id),
            org_id=None,
            actor_id=superadmin.id,
            diff={"reason": reason, "duration_minutes": minutes},
            ip=ip,
            ua=ua,
            severity="elevated",
        )
        return access_token, utcnow() + timedelta(minutes=minutes)

    async def end(
        self, *, token: str, ip: str | None, ua: str | None, channel: ImpersonationChannel = "web"
    ) -> None:
        payload = verify_access_token(token)
        if payload.impersonator_id is None:
            raise DomainError(
                "errors.not_impersonating",
                fallback="This token is not an impersonation session",
            )
        await block_jti(self._redis, payload.jti, MAX_IMPERSONATION_MINUTES * 60)
        await self._audit.log(
            # T-261 asks for the mobile ending to be distinguishable in the
            # feed from the web one, and it genuinely is a different fact:
            # ending on the phone leaves the superadmin logged out there (one
            # session per install), while ending on web silently falls back to
            # their own refresh cookie. Same event, two names, rather than one
            # name and a diff field, because that is what an operator reading
            # the feed filters on.
            action=_END_ACTIONS[channel],
            resource_type="user",
            resource_id=str(payload.user_id),
            org_id=None,
            actor_id=payload.user_id,
            diff={},
            ip=ip,
            ua=ua,
            severity="elevated",
        )


class AdminMobileHandoffService:
    """T-261: moves an impersonation session that already exists onto the
    superadmin's own phone, so a mobile-only bug can be reproduced without
    asking the user to hand over their device.

    It mints nothing. The access token it hands to the phone is the *same*
    token T-142 already issued -- same jti, same `exp`, same
    `impersonator_id`, so it is already covered by the blocklist (T-142), the
    destructive-action guard (T-260), the audit stamping, and the one-hour
    cap, with no second code path that could drift from any of them. All this
    service adds is a two-minute, single-use bearer for *that* token, short
    enough to be safe on a screen and in an OS deep-link handler.

    The exchange leg is unauthenticated by necessity -- a phone that has not
    adopted the session yet has no credential to present but the exchange
    token itself -- which is why that token is 32 random bytes, consumed
    atomically (GETDEL), stored only as a hash, and dead two minutes after it
    is minted whether or not anyone uses it.
    """

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._redis = redis
        self._audit = AuditService(session)

    async def create(
        self, *, token: str, target: User, ip: str | None, ua: str | None
    ) -> tuple[str, datetime]:
        """Returns (deep_link, expires_at) for an active impersonation session.

        `token` is the caller's own bearer token: the route this runs behind
        already put it through get_current_user, so it is live, unexpired and
        not blocklisted by the time it reaches here.
        """
        payload = verify_access_token(token)
        if payload.impersonator_id is None:
            raise DomainError(
                "errors.not_impersonating",
                fallback="This token is not an impersonation session",
            )
        exchange_token = generate_exchange_token()
        record = HandoffRecord(
            access_token=token,
            user_id=payload.user_id,
            user_email=target.email,
            impersonator_id=payload.impersonator_id,
            reason=await get_session_reason(self._redis, payload.jti) or "",
            session_expires_at=payload.expires_at,
            expires_at=handoff_expiry(),
        )
        await store_handoff(self._redis, exchange_token, record)
        await self._audit.log(
            action="impersonation.mobile_handoff_created",
            resource_type="user",
            resource_id=str(payload.user_id),
            org_id=None,
            actor_id=payload.user_id,
            diff={"expires_at": record.expires_at.isoformat()},
            ip=ip,
            ua=ua,
            severity="elevated",
        )
        return _deep_link(exchange_token, target.email), record.expires_at

    async def preview(self, *, exchange_token: str) -> HandoffRecord:
        """What the phone shows before the operator commits: who, why, how
        long is left. Does not consume the token -- opening the link twice
        (an OS that re-delivers it, a backgrounded app) must not burn the one
        redemption, and this leg reveals nothing to a holder who could simply
        redeem it instead."""
        record = await peek_handoff(self._redis, exchange_token)
        if record is None:
            raise AuthenticationError(
                "errors.invalid_exchange_token",
                fallback="This handoff link is invalid or has expired",
            )
        return record

    async def exchange(
        self, *, exchange_token: str, ip: str | None, ua: str | None
    ) -> HandoffRecord:
        record = await take_handoff(self._redis, exchange_token)
        if record is None:
            raise AuthenticationError(
                "errors.invalid_exchange_token",
                fallback="This handoff link is invalid or has expired",
            )
        await self._log_as_impersonator(
            record,
            action="impersonation.mobile_handoff",
            diff={"session_expires_at": record.session_expires_at.isoformat()},
            ip=ip,
            ua=ua,
        )
        return record

    async def cancel(self, *, exchange_token: str, ip: str | None, ua: str | None) -> None:
        """The phone's Cancel button. Idempotent and silent about unknown
        tokens: this leg is unauthenticated, so answering "that one existed"
        differently from "that one didn't" would turn it into an oracle for
        guessing live handoffs.

        Consumes the record through the same GETDEL as `exchange` rather than
        reading it and then deleting it: cancel and exchange race each other
        whenever two devices open the same link, and a peek-then-delete cancel
        would happily log `impersonation.mobile_handoff_cancelled` for a
        handoff the other device had already adopted -- an audit trail saying
        the operator declined a session they are, at that moment, using.
        Exactly one of the two calls can win the GETDEL.
        """
        record = await take_handoff(self._redis, exchange_token)
        if record is None:
            return
        await self._log_as_impersonator(
            record, action="impersonation.mobile_handoff_cancelled", diff={}, ip=ip, ua=ua
        )

    async def _log_as_impersonator(
        self,
        record: HandoffRecord,
        *,
        action: str,
        diff: dict[str, Any],
        ip: str | None,
        ua: str | None,
    ) -> None:
        """Writes an event stamped with the superadmin behind the session.

        The exchange and cancel legs carry no Authorization header, so
        app.middleware.impersonation has nothing to decode and
        AuditService.log would otherwise record these two events -- the only
        two in the impersonation trail that are *not* made by the impersonated
        principal's own token -- with a blank impersonator_id. Setting the
        same ContextVar the middleware sets keeps "who was really acting" a
        thing the audit layer reads rather than a thing every caller may pass,
        which is the invariant app.core.audit_context exists to hold.
        """
        context_token = set_impersonator_id(record.impersonator_id)
        try:
            await self._audit.log(
                action=action,
                resource_type="user",
                resource_id=str(record.user_id),
                org_id=None,
                actor_id=record.user_id,
                diff=diff,
                ip=ip,
                ua=ua,
                severity="elevated",
            )
        finally:
            reset_impersonator_id(context_token)


class AdminAccountRecoveryService:
    """T-218/ASVS V2.5.7: admin-assisted recovery for a user who's lost both
    their TOTP device and every backup code. Same posture as
    AdminImpersonationService above -- a mandatory `reason`, fully audited at
    severity="elevated" -- since a superadmin unilaterally turning off a
    user's second factor is exactly the kind of high-value action that
    posture exists for.

    Deliberately scoped to TOTP only, not WebAuthn: `AuthService.login()`'s
    2FA gate already treats a registered security key as an alternative to
    TOTP, not an additional factor (T-217), so a user who still has one
    isn't locked out and doesn't need this path -- they can just use it.
    Reaches directly into app.modules.auth's repositories rather than
    AuthService itself, same justification as AdminBillingOverrideService's
    docstring above: this is a cross-cutting admin action, not something the
    auth module's own self-service surface should expose.
    """

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._redis = redis
        self._users = UserRepository(session)
        self._totp_secrets = TOTPSecretRepository(session)
        self._audit = AuditService(session)

    async def disable_totp_for_user(
        self, *, target: User, reason: str, actor_id: UUID, ip: str | None, ua: str | None
    ) -> None:
        if not target.totp_enabled:
            raise ConflictError(
                "errors.totp_not_enabled", fallback="Two-factor authentication is not enabled"
            )

        record = await self._totp_secrets.get_by_user_id(target.id)
        if record is not None:
            await self._totp_secrets.disable(target.id)
        await self._users.update(target.id, totp_enabled=False)
        # T-240: totp_enabled lives on the cached User row, and
        # require_superadmin gates on it.
        await invalidate_user_cache(self._redis, target.id)

        await self._audit.log(
            action="admin.user.disable_totp",
            resource_type="user",
            resource_id=str(target.id),
            org_id=None,
            actor_id=actor_id,
            diff={"reason": reason},
            ip=ip,
            ua=ua,
            severity="elevated",
        )
        await jobs.enqueue_job(
            "send_email",
            to=target.email,
            template="security_notification",
            locale=target.locale,
            context={
                "user_name": target.full_name,
                "event": "totp_disabled_by_admin",
                "provider": None,
                "new_email": None,
                "settings_url": frontend_url("/settings/security", target.locale),
            },
        )
