"""T-291 combination C: the three Phase 4 modules on, everything else default.

CI's `module-matrix` job copies this over `backend/product/config.py` (and
`phase4.config.ts` over the repo-root `product.config.ts`). Two of the three
ship off by default (T-290), so without this combination nothing would ever
migrate, boot or build with them enabled until T-310/T-331 land.

Everything below except `MODULES` is a verbatim copy of the committed
`backend/product/config.py`; `tests/test_product_config_parity.py` fails if the
two drift.
"""

from app.core.locales import EnabledLocales, LocaleConfig
from app.core.modules import EnabledModules, ModuleConfig
from app.core.support import SupportTool

# ── Module toggles ──────────────────────────────────────────────────────────
# The template's defaults, with `public_api` and `enterprise_sso` flipped on
# (`marketing` already is). `enterprise_sso` requires `oauth`, which is on
# here -- the illegal half of that pair is asserted in
# tests/modules/test_module_toggles.py rather than shipped as a fixture, since
# a fixture that cannot boot is not a combination to run.
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
    outbound_webhooks=ModuleConfig(enabled=False),
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
