import {
  BarChart3,
  Bell,
  Building2,
  CreditCard,
  FolderOpen,
  KeyRound,
  LayoutDashboard,
  Shield,
  User as UserIcon,
  Users,
  Webhook,
} from "lucide-react";

import { useFeatureFlags, useModules, useOrg } from "@forge/hooks";
import type { ActiveModules } from "@forge/hooks";
import { Permission, ROLE_PERMISSIONS } from "@forge/types";

// T-233: every in-app destination is locale-prefixed. `to` is the router's
// path (with the `$locale` param it needs); `path` is the same destination
// without it, and is what the breadcrumb, the active-state check, and the
// `data-testid` slugs are keyed on -- none of those should change shape just
// because the URL grew a locale segment.
export type NavTo =
  | "/$locale"
  | "/$locale/files"
  | "/$locale/reports"
  | "/$locale/settings/profile"
  | "/$locale/settings/security"
  | "/$locale/settings/org"
  | "/$locale/settings/members"
  | "/$locale/settings/billing"
  | "/$locale/settings/notifications"
  | "/$locale/settings/api-keys"
  | "/$locale/settings/webhooks";

export interface NavItem {
  to: NavTo;
  path: string;
  labelKey: string;
  icon: typeof LayoutDashboard;
  permission?: Permission;
  module?: keyof ActiveModules;
  // Org-plan-driven gate (T-121), orthogonal to `module` (product-config-driven)
  // -- both a disabled module and a plan without this flag hide the item.
  flag?: string;
  /** Settings only: which sidebar group the item sits under. Personal settings
   *  are "account"; everything shared by the Organization is "organization". */
  group?: "account" | "organization";
}

/**
 * The app's navigation, in one place (F-005).
 *
 * This used to live inside AppLayout.tsx as two module-private consts. The
 * command palette needs the same list -- a palette that offers a page the
 * sidebar hides, or misses one it shows, is worse than no palette, because it
 * is the surface users reach for when they cannot find something. Two copies
 * of a gated list drift the first time a module gains a page.
 */
export const PRIMARY_NAV: NavItem[] = [
  {
    to: "/$locale",
    path: "/",
    labelKey: "common.nav.dashboard",
    icon: LayoutDashboard,
  },
  {
    to: "/$locale/files",
    path: "/files",
    labelKey: "common.nav.files",
    icon: FolderOpen,
    module: "fileStorage",
  },
  {
    to: "/$locale/reports",
    path: "/reports",
    labelKey: "common.nav.reports",
    icon: BarChart3,
    module: "featureFlags",
    flag: "advanced_reports",
  },
];

export const SETTINGS_NAV: NavItem[] = [
  {
    to: "/$locale/settings/profile",
    path: "/settings/profile",
    group: "account",
    labelKey: "common.nav.profile",
    icon: UserIcon,
  },
  {
    to: "/$locale/settings/security",
    path: "/settings/security",
    group: "account",
    labelKey: "common.nav.security",
    icon: Shield,
  },
  {
    to: "/$locale/settings/org",
    path: "/settings/org",
    group: "organization",
    labelKey: "common.nav.organization",
    icon: Building2,
  },
  {
    to: "/$locale/settings/members",
    path: "/settings/members",
    group: "organization",
    labelKey: "common.nav.members",
    icon: Users,
  },
  {
    to: "/$locale/settings/billing",
    path: "/settings/billing",
    group: "organization",
    labelKey: "common.nav.billing",
    icon: CreditCard,
    permission: Permission.BILLING_READ,
    module: "billing",
  },
  {
    to: "/$locale/settings/notifications",
    path: "/settings/notifications",
    group: "account",
    labelKey: "common.nav.notifications",
    icon: Bell,
    module: "notifications",
  },
  // T-313/T-322: both are org-manage surfaces (a key that can mint a key and
  // an endpoint that can be pointed anywhere are each an escalation), so the
  // permission gate is SETTINGS_MANAGE on top of the module toggle -- the
  // same pair their routers use.
  {
    to: "/$locale/settings/api-keys",
    path: "/settings/api-keys",
    group: "organization",
    labelKey: "common.nav.api_keys",
    icon: KeyRound,
    permission: Permission.SETTINGS_MANAGE,
    module: "publicApi",
  },
  {
    to: "/$locale/settings/webhooks",
    path: "/settings/webhooks",
    group: "organization",
    labelKey: "common.nav.webhooks",
    icon: Webhook,
    permission: Permission.SETTINGS_MANAGE,
    module: "outboundWebhooks",
  },
];

export interface VisibleNav {
  primary: NavItem[];
  settings: NavItem[];
  /** Both lists concatenated -- what the command palette offers. */
  all: NavItem[];
}

/**
 * The nav items this user can actually reach, with all three gates applied.
 *
 * Resolves each gate *once* for the whole list rather than once per item.
 * The per-item version (a <NavLink> calling usePermission() and
 * useFeatureFlag() for itself) worked only because each link is its own
 * component; calling those hooks in a `.map()` would be a
 * react-hooks/rules-of-hooks violation the moment a module toggle changed the
 * list's length between renders. Consulting the three underlying sources
 * directly has no such constraint, and is the same comparison.
 *
 * Deny-by-default throughout, matching useFeatureFlag and useModules: no role
 * yet, no flags yet, or a failed fetch all read as "hide it".
 */
export function useVisibleNavItems(): VisibleNav {
  const modules = useModules();
  const { role } = useOrg();
  const { data: flagData } = useFeatureFlags();
  const flags = flagData?.data?.flags;

  const isVisible = (item: NavItem): boolean => {
    if (item.module && !modules[item.module]) {
      return false;
    }
    if (item.permission) {
      if (!role || !ROLE_PERMISSIONS[role].has(item.permission)) {
        return false;
      }
    }
    if (item.flag && !flags?.[item.flag]) {
      return false;
    }
    return true;
  };

  const primary = PRIMARY_NAV.filter(isVisible);
  const settings = SETTINGS_NAV.filter(isVisible);
  return { primary, settings, all: [...primary, ...settings] };
}
