"""
Product configuration — this file is owned by the product, not the template.
The template never modifies it. Edit freely.
"""

from app.core.locales import EnabledLocales, LocaleConfig
from app.core.modules import EnabledModules, ModuleConfig

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
)

# ── Branding ────────────────────────────────────────────────────────────────
APP_NAME = "ForgeSaaS"
PRIMARY_COLOR = "#7c6ff7"
LOGO_URL = "/logo.svg"
SUPPORT_EMAIL = "support@example.com"

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
