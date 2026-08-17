"""
Product configuration — this file is owned by the product, not the template.
The template never modifies it. Edit freely.
"""

from app.core.locales import EnabledLocales, LocaleConfig
from app.core.modules import EnabledModules, ModuleConfig
from app.core.support import SupportTool

# ── Module toggles ──────────────────────────────────────────────────────────
# Set enabled=False for any module you do not need.
# The module's routes, DB tables, and UI will all be excluded automatically.
# See TEMPLATE.md for dependency rules (e.g. notifications requires billing).
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
    # Off removes the whole apps/marketing build (its `build` script exits 0
    # without producing a dist) and the Caddy block that serves it. The app
    # itself is untouched -- links that would point at the marketing site fall
    # back to the app root.
    marketing=ModuleConfig(enabled=True),
    # Off removes the API-key surface entirely: no /api/v1/api-keys routes, no
    # `api_key` table, no second kind of principal that can authenticate. Turn
    # it on only when third parties actually need to call you.
    public_api=ModuleConfig(enabled=False),
    # Off removes per-org OIDC/SAML sign-in (and keeps python3-saml's native
    # libxmlsec1 dependency out of the backend image). Requires oauth -- see
    # EnabledModules in app/core/modules.py.
    enterprise_sso=ModuleConfig(enabled=False),
)

# ── Branding ────────────────────────────────────────────────────────────────
APP_NAME = "ForgeSaaS"
PRIMARY_COLOR = "#7c6ff7"
LOGO_URL = "/logo.svg"
SUPPORT_EMAIL = "support@example.com"

# ── Support tool (T-262) ────────────────────────────────────────────────────
# Which support tool this product uses. The template stays tool-agnostic: it
# surfaces the signed-in user's context (GET /api/v1/me/support-context and
# packages/hooks' useSupportContext) and renders a "Contact support" entry,
# but never imports a vendor SDK. Booting the widget itself is product-side
# code -- docs/runbooks/support-tool-integration.md walks Intercom, Crisp and
# Plain end to end.
#   "none"                      -> the entry is a plain link to SUPPORT_URL
#   "intercom" | "crisp" |      -> the entry asks the product's widget to open
#   "plain" | "helpscout" |        (falling back to the link if nothing is
#   "custom"                       listening -- e.g. the widget was blocked)
SUPPORT_TOOL: SupportTool = "none"
# Where that link points. None falls back to `mailto:SUPPORT_EMAIL`; set it
# to a help centre or a hosted contact form instead if you have one. Must be
# an https://, http:// or mailto: URL.
SUPPORT_URL: str | None = None

# ── Internationalisation ────────────────────────────────────────────────────
# Each locale toggle mirrors MODULES above -- enabled=False excludes it from
# get_supported_locales() (and therefore the /config endpoint and web/mobile
# bundles) without deleting its translation data, same as a disabled module's
# routes/tables staying inert rather than removed. Every code here must have
# a directory under packages/i18n/locales/ -- see packages/i18n/README.md for
# the key-naming conventions every translatable string follows. Keep this in
# sync with product.config.ts's `locales` field.
LOCALES = EnabledLocales(
    en=LocaleConfig(enabled=True),
    fi=LocaleConfig(enabled=True),
    de=LocaleConfig(enabled=True),
    es=LocaleConfig(enabled=True),
    fr=LocaleConfig(enabled=True),
    ar=LocaleConfig(enabled=True),
)
DEFAULT_LOCALE = "en"
