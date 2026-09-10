"""T-291 combination B: every optional module off — the minimum-surface product.

CI's `module-matrix` job copies this over `backend/product/config.py` (and
`minimal.config.ts` over the repo-root `product.config.ts`), then migrates from
an empty database, boots the app, and builds the frontend. This is the
configuration a template user is most likely to want and least likely to have
been tested — see this directory's README.md.

Everything below except `MODULES` is a verbatim copy of the committed
`backend/product/config.py`; `tests/test_product_config_parity.py` fails if the
two drift.
"""

from app.core.locales import EnabledLocales, LocaleConfig
from app.core.modules import EnabledModules, ModuleConfig
from app.core.plans import PlanDefinition
from app.core.support import SupportTool

# ── Module toggles ──────────────────────────────────────────────────────────
# Every one of the fourteen, off. This is legal precisely because the four
# modules that carry a `requires` edge (notifications, file_storage,
# usage_metering, outbound_webhooks -> billing; enterprise_sso -> oauth) go off
# with the module they depend on: "everything off" is always a valid
# configuration, while "billing off, notifications on" never is.
MODULES = EnabledModules(
    billing=ModuleConfig(enabled=False),
    notifications=ModuleConfig(enabled=False),
    file_storage=ModuleConfig(enabled=False),
    feature_flags=ModuleConfig(enabled=False),
    gdpr=ModuleConfig(enabled=False),
    admin=ModuleConfig(enabled=False),
    oauth=ModuleConfig(enabled=False),
    totp_2fa=ModuleConfig(enabled=False),
    multi_org=ModuleConfig(enabled=False),
    usage_metering=ModuleConfig(enabled=False),
    outbound_webhooks=ModuleConfig(enabled=False),
    marketing=ModuleConfig(enabled=False),
    public_api=ModuleConfig(enabled=False),
    enterprise_sso=ModuleConfig(enabled=False),
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
