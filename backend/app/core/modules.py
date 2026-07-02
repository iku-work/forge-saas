from __future__ import annotations

from dataclasses import dataclass, field
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
