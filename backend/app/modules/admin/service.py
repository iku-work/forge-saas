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
from uuid import UUID, uuid4

import stripe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import DomainError, NotFoundError, PermissionDeniedError
from app.core.module_loader import is_enabled
from app.core.repository import DEFAULT_PAGE_SIZE
from app.core.token_blocklist import block_jti
from app.models._mixins import utcnow
from app.modules.admin.repository import AdminOrgRepository
from app.modules.admin.schemas import (
    AdminAuditEntry,
    AdminAuditSummary,
    AdminFeatureFlagState,
    AdminMembershipSummary,
    AdminOrgDetail,
    AdminOrgSummary,
    AdminSessionSummary,
    AdminSubscriptionDetail,
    AdminUserDetail,
    AdminUserSummary,
)
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.repository import SessionRepository, UserRepository
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
            file_count = await StoredFileRepository(
                self._session, org.id, self._redis
            ).count_files_for_org()

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
        membership_summaries = []
        for membership in memberships:
            org = await OrganizationRepository(self._session).get_by_id(membership.org_id)
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
        if target.is_superadmin:
            raise PermissionDeniedError(
                "errors.cannot_impersonate_superadmin",
                fallback="Cannot impersonate another superadmin",
            )
        minutes = min(duration_minutes, MAX_IMPERSONATION_MINUTES)
        access_token = create_access_token(
            target.id, None, (), impersonator_id=superadmin.id, expire_minutes=minutes
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

    async def end(self, *, token: str, ip: str | None, ua: str | None) -> None:
        payload = verify_access_token(token)
        if payload.impersonator_id is None:
            raise DomainError(
                "errors.not_impersonating",
                fallback="This token is not an impersonation session",
            )
        await block_jti(self._redis, payload.jti, MAX_IMPERSONATION_MINUTES * 60)
        await self._audit.log(
            action="admin.impersonate.end",
            resource_type="user",
            resource_id=str(payload.user_id),
            org_id=None,
            actor_id=payload.user_id,
            diff={},
            ip=ip,
            ua=ua,
            severity="elevated",
        )
