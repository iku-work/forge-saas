"""T-291 combination C: every off-by-default Phase 4 module on.

CI's `module-matrix` job copies this over `backend/product/config.py` (and
`phase4.config.ts` over the repo-root `product.config.ts`). Three of these
ship off by default (T-290/T-320), so without this combination nothing would
ever migrate, boot or build with them enabled -- and the sweeps that walk
`app.routes` (T-243's query budgets, T-260's impersonation blocklist, T-049's
cross-tenant probe, T-050's schemathesis run) would never see their routes.

Everything below except `MODULES` is a verbatim copy of the committed
`backend/product/config.py`; `tests/test_product_config_parity.py` fails if the
two drift.
"""

from app.core.locales import EnabledLocales, LocaleConfig
from app.core.modules import EnabledModules, ModuleConfig
from app.core.plans import PlanDefinition
from app.core.support import SupportTool

# ── Module toggles ──────────────────────────────────────────────────────────
# The template's defaults, with `public_api`, `outbound_webhooks` and
# `enterprise_sso` flipped on (`marketing` already is). Both
# `outbound_webhooks` and `enterprise_sso` have `requires` edges (billing and
# oauth respectively), and both are satisfied here -- the illegal halves of
# those pairs are asserted in tests/modules/test_module_toggles.py rather than
# shipped as fixtures, since a fixture that cannot boot is not a combination
# to run.
MODULES = EnabledModules(
    billing=ModuleConfig(enabled=True),
    notifications=ModuleConfig(enabled=True),
    file_storage=ModuleConfig(enabled=True),
    feature_flags=ModuleConfig(enabled=True),
    gdpr=ModuleConfig(enabled=True),
    admin=ModuleConfig(enabled=True),
    oauth=ModuleConfig(enabled=True),
    totp_2fa=ModuleConfig(enabled=True),
    multi_org=ModuleConfig(enabled=True),
    usage_metering=ModuleConfig(enabled=False),
    outbound_webhooks=ModuleConfig(enabled=True),
    marketing=ModuleConfig(enabled=True),
    public_api=ModuleConfig(enabled=True),
    enterprise_sso=ModuleConfig(enabled=True),
)

# ── Branding ────────────────────────────────────────────────────────────────
APP_NAME = "ForgeSaaS"
PRIMARY_COLOR = "#7c6ff7"
LOGO_URL = "/logo.svg"
SUPPORT_EMAIL = "support@example.com"

# ── Support tool (T-262) ────────────────────────────────────────────────────
SUPPORT_TOOL: SupportTool = "none"
SUPPORT_URL: str | None = None

# ── Internationalisation ────────────────────────────────────────────────────
LOCALES = EnabledLocales(
    en=LocaleConfig(enabled=True),
    fi=LocaleConfig(enabled=True),
    de=LocaleConfig(enabled=True),
    es=LocaleConfig(enabled=True),
    fr=LocaleConfig(enabled=True),
    ar=LocaleConfig(enabled=True),
)
DEFAULT_LOCALE = "en"


# ── Billing plans ───────────────────────────────────────────────────────────
# What each plan GRANTS. What it COSTS, and what it is called, live in Stripe
# -- these are joined by `lookup_key`, a stable name you set on the Stripe
# Price, so no opaque `price_1AbC...` id is ever typed into this repo. Run
# `make sync-plans` after editing this list or after changing prices in the
# Stripe dashboard; it resolves each lookup key and writes the ids into the
# plan table. See app/core/plans.py for why the split falls here.
#
# Only paid plans belong here. "Free" is not a Stripe product and never needs
# one -- an org with no subscription resolves to the in-memory _FREE_PLAN in
# app/modules/billing/dependencies.py, which is where the free tier's own
# limits are set.
PLANS = [
    PlanDefinition(
        slug="pro",
        lookup_key_monthly="pro_monthly",
        lookup_key_yearly="pro_yearly",
        features={
            "seats": 25,
            "storage_gb": 50,
            "api_calls": 100000,
            "advanced_reports": True,
            "file_storage": True,
        },
    ),
]
