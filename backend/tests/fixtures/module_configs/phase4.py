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
