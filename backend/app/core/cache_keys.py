"""
Every Redis *cache* key the backend writes, built in one place (T-240).

The rule this module exists to enforce: no call site anywhere in `app/`
builds a cache key with an inline f-string. A key that only exists as a
literal at its read site is a key nobody can find when they add the write
path that has to bust it -- which is how a cache-invalidation bug gets
written. tests/test_cache_keys.py greps for violations.

Non-cache Redis keys are deliberately *not* here: the token blocklist
(app.core.token_blocklist), the scheduler's distributed locks
(app.workers.scheduler), the rate limiter's identity key
(app.middleware.rate_limit) and the SSE pub/sub channels
(app.modules.notifications.sse) are state, not cached copies of a row, and
none of them has an invalidation matrix to keep honest.

--- Invalidation matrix (T-240) ---------------------------------------------

user_key -- User row behind get_current_user. Busted by every write to that
row a request can reach:
  * profile/locale update ............ auth.router.update_me
  * notification preferences ......... auth.router.update_notification_preferences
  * email change confirmed ........... auth.router.confirm_email_change
  * password change / reset .......... auth.router.change_password / reset_password
  * TOTP enable / disable ............ auth.router.confirm_totp / disable_totp
  * TOTP disabled by an admin ........ admin.router.disable_user_totp
  * email verified (is_active flip) .. auth.router.verify_email
  * deactivation + GDPR delete ....... gdpr.router.delete_me, workers.jobs.gdpr.purge_user_data
  * role / membership change ......... tenancy.router.update_member_role / remove_member
  (is_superadmin has no write path in the product -- it is set by seeding or
  by hand in the DB -- so there is nothing to wire for it; if an endpoint for
  it ever lands, it belongs on this list.)

org_settings_key -- Organization row behind get_current_org:
  * org update ....................... tenancy.router.update_org
  * stripe_customer_id assigned ...... billing.service.get_or_create_stripe_customer
  * org billing detached on purge .... workers.jobs.gdpr.purge_user_data
  (org deletion has no endpoint either -- Organization is soft-deletable but
  nothing exposes it.)

org_plan_key -- resolved Subscription+Plan behind the feature gate. Always
busted together with the feature-flag keys below (a flag is *derived* from
the plan, so the two can never be allowed to disagree) -- see
app.core.cache.invalidate_org_entitlements, which is the only thing that
deletes either:
  * checkout completed / subscription updated / canceled / payment
    succeeded / payment failed ....... billing.service.handle_event
  * admin plan override, trial extension ... admin.service.AdminBillingOverrideService
  * dunning day-7 downgrade .......... workers.jobs.billing.send_dunning_email
  * trial expiry sweep ............... workers.jobs.billing.expire_trials_to_inactive
  * feature-flag override toggled .... feature_flags.service.set_override / delete_override

api_key_lookup_key -- the ApiKey row behind T-311's authentication path,
keyed by the *prefix* the caller presented (never by the secret, and never by
anything derived from it -- a Redis key is not a secret store, and this one
appears in SLOWLOG and in `KEYS` output). It is the hottest read in the
public API: every request that presents a key resolves this before anything
else runs. Busted by every write that changes whether a key authenticates, or
with what:
  * key created ...................... api_keys.service.ApiKeyService.create_key
  * key revoked ...................... api_keys.service.ApiKeyService.revoke_key
  * rate-limit override changed ...... api_keys.service.ApiKeyService.set_rate_limit
  * key erased with its org's user ... workers.jobs.gdpr.purge_user_data
  (`last_used_at` is deliberately *not* on this list -- the throttled touch
  writes a column nothing about authentication reads, so busting the entry
  for it would turn a once-a-minute write into a once-a-request cache miss,
  which is the whole cost this cache exists to avoid. The cached payload
  therefore carries a `last_used_at` that lags by up to the TTL, and nothing
  reads it from there.)

product_settings_key -- T-263's global singleton row, as a whole dict rather
than a key at a time (it is one row, and the maintenance middleware reads it
on every request, so one Redis GET has to answer every question about it):
  * any operator write ............... product_settings.service.ProductSettingsService.set
                                       / replace_all -- both go through
                                       _store, which is the only writer and
                                       busts this key before it returns.
  (There is no other write path. The row is never written by a job, a webhook
  or a user-facing endpoint -- if one is ever added, it belongs on this list,
  because a stale maintenance flag is an outage that outlives the fix.)
"""

from uuid import UUID

# Namespaces, kept as constants so tests/test_cache_keys.py can assert no
# other module builds a string starting with one of them.
USER_NAMESPACE = "user"
ORG_NAMESPACE = "org"
FEATURE_FLAGS_NAMESPACE = "feature_flags"
INVOICES_NAMESPACE = "invoices"
NOTIFICATIONS_NAMESPACE = "notif"
STORAGE_NAMESPACE = "storage"
PRODUCT_SETTINGS_NAMESPACE = "product_settings"
API_KEY_NAMESPACE = "api_key"

CACHE_NAMESPACES: tuple[str, ...] = (
    USER_NAMESPACE,
    ORG_NAMESPACE,
    FEATURE_FLAGS_NAMESPACE,
    INVOICES_NAMESPACE,
    NOTIFICATIONS_NAMESPACE,
    STORAGE_NAMESPACE,
    PRODUCT_SETTINGS_NAMESPACE,
    API_KEY_NAMESPACE,
)


def user_key(user_id: UUID) -> str:
    return f"{USER_NAMESPACE}:{user_id}"


def org_settings_key(org_id: UUID) -> str:
    return f"{ORG_NAMESPACE}:{org_id}:settings"


def org_plan_key(org_id: UUID) -> str:
    return f"{ORG_NAMESPACE}:{org_id}:plan"


def feature_flag_key(org_id: UUID, flag: str) -> str:
    return f"{FEATURE_FLAGS_NAMESPACE}:{org_id}:{flag}"


def feature_flags_all_key(org_id: UUID) -> str:
    return f"{FEATURE_FLAGS_NAMESPACE}:{org_id}:all"


def feature_flags_pattern(org_id: UUID) -> str:
    return f"{FEATURE_FLAGS_NAMESPACE}:{org_id}:*"


def invoices_key(org_id: UUID, cursor: str | None) -> str:
    return f"{INVOICES_NAMESPACE}:{org_id}:{cursor or 'first'}"


def invoices_pattern(org_id: UUID) -> str:
    return f"{INVOICES_NAMESPACE}:{org_id}:*"


def notification_unread_count_key(user_id: UUID) -> str:
    return f"{NOTIFICATIONS_NAMESPACE}:unread:{user_id}"


def storage_total_bytes_key(org_id: UUID) -> str:
    return f"{STORAGE_NAMESPACE}:total_bytes:{org_id}"


def product_settings_key() -> str:
    """T-263. Takes no argument -- there is exactly one settings row, and the
    key is a function anyway so it lives under the same no-inline-literals
    rule tests/test_cache_keys.py enforces for every other key here."""
    return f"{PRODUCT_SETTINGS_NAMESPACE}:all"


def api_key_lookup_key(prefix: str) -> str:
    """T-310/T-311. Keyed by the clear-text prefix, which is what the
    authentication path has in hand before it has a row -- see the
    invalidation matrix above for why `last_used_at` does not bust it."""
    return f"{API_KEY_NAMESPACE}:{prefix}"
