from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from typing import Literal

ModuleName = Literal[
    "billing",
    "notifications",
    "file_storage",
    "feature_flags",
    "gdpr",
    "admin",
    "oauth",
    "totp_2fa",
    "multi_org",
    "usage_metering",
    "outbound_webhooks",
    "marketing",
    "public_api",
    "enterprise_sso",
]


@dataclass(frozen=True)
class ModuleConfig:
    enabled: bool = True
    requires: list[ModuleName] = field(default_factory=list)


@dataclass(frozen=True)
class EnabledModules:
    billing: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    notifications: ModuleConfig = field(
        default_factory=lambda: ModuleConfig(enabled=True, requires=["billing"])
    )
    file_storage: ModuleConfig = field(
        default_factory=lambda: ModuleConfig(enabled=True, requires=["billing"])
    )
    feature_flags: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    gdpr: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    admin: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    oauth: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    totp_2fa: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    multi_org: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    usage_metering: ModuleConfig = field(
        default_factory=lambda: ModuleConfig(enabled=False, requires=["billing"])
    )
    outbound_webhooks: ModuleConfig = field(
        default_factory=lambda: ModuleConfig(enabled=False, requires=["billing"])
    )
    # T-290 (Phase 4). The three defaults below differ from each other on
    # purpose -- each answers "what does a product that never thinks about
    # this get?":
    #   * nearly every product wants a landing page, so `marketing` is on;
    #   * an org-scoped API key is attack surface nobody asked for, so
    #     `public_api` is off;
    #   * SAML drags python3-saml -> xmlsec -> native libxmlsec1 into the
    #     backend image, so `enterprise_sso` is off.
    marketing: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
    public_api: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=False))
    # Requires `oauth` because T-331's OIDC SP reuses authlib's client registry
    # and T-025's account-linking. validate_modules() fails the boot on the
    # illegal combination rather than letting an SSO login find half a stack.
    enterprise_sso: ModuleConfig = field(
        default_factory=lambda: ModuleConfig(enabled=False, requires=["oauth"])
    )


def declared_requirements() -> dict[str, tuple[ModuleName, ...]]:
    """Every dependency edge this file declares, keyed by module name.

    The edges belong to the *template* -- "notifications cannot work without
    billing" is a fact about the code, not a choice a product makes -- but
    they are written as `requires` on each field's default. A product config
    that spells a module out (`notifications=ModuleConfig(enabled=True)`,
    which is exactly how backend/product/config.py writes every entry) builds
    a fresh ModuleConfig whose `requires` defaults to empty, silently dropping
    the edge: the default_factory only runs for fields the caller *omits*.

    So validate_modules() reads the edges from here rather than from the
    instance it was handed, and unions in whatever that instance declares of
    its own (a product-specific module may legitimately add one).
    """
    requirements: dict[str, tuple[ModuleName, ...]] = {}
    for module_field in fields(EnabledModules):
        factory = module_field.default_factory
        if factory is MISSING:
            continue
        default = factory()
        if isinstance(default, ModuleConfig):
            requirements[module_field.name] = tuple(default.requires)
    return requirements
