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
