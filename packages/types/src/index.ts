// Mirrors backend/app/modules/auth/permissions.py — keep both in sync.
export enum Permission {
  MEMBERS_READ = "members:read",
  MEMBERS_MANAGE = "members:manage",
  BILLING_READ = "billing:read",
  BILLING_MANAGE = "billing:manage",
  FILES_READ = "files:read",
  FILES_MANAGE = "files:manage",
  AUDIT_READ = "audit:read",
  SETTINGS_MANAGE = "settings:manage",
}

export enum MemberRole {
  OWNER = "owner",
  ADMIN = "admin",
  MEMBER = "member",
}

// Mirrors ROLE_PERMISSIONS in backend/app/modules/auth/permissions.py. The
// backend is authoritative -- this only drives client-side UI gating (e.g.
// hiding a button a member has no permission to use); the API rejects the
// request regardless of what the UI shows.
//
// Keyed by `${MemberRole}` (the union of the enum's string values) rather
// than MemberRole itself: API responses (e.g. MembershipPublic.role in
// @forge/api-client) come back typed as the plain string-literal union
// generated from the OpenAPI schema, not this enum, and a string enum's
// member type isn't assignable from -- so indexing this map with a
// generated-type role wouldn't type-check without it.
export const ROLE_PERMISSIONS: Readonly<Record<`${MemberRole}`, ReadonlySet<Permission>>> = {
  [MemberRole.OWNER]: new Set(Object.values(Permission)),
  [MemberRole.ADMIN]: new Set([
    Permission.MEMBERS_READ,
    Permission.MEMBERS_MANAGE,
    Permission.BILLING_READ,
    Permission.FILES_READ,
    Permission.FILES_MANAGE,
    Permission.AUDIT_READ,
    Permission.SETTINGS_MANAGE,
  ]),
  [MemberRole.MEMBER]: new Set([Permission.MEMBERS_READ, Permission.FILES_READ]),
};

export type SubscriptionStatus =
  | "trialing"
  | "active"
  | "past_due"
  | "canceled"
  | "incomplete";

// Mirrors backend/app/modules/notifications/preferences.py — keep both in
// sync. There is no endpoint that exposes these as data (they're baked into
// the backend's validator), so the settings page renders its checkbox
// matrix off this client-side copy.
export const NOTIFICATION_CHANNELS = ["email", "in_app", "push"] as const;
export type NotificationChannel = (typeof NOTIFICATION_CHANNELS)[number];

export const NOTIFICATION_TYPES = [
  "member_invited",
  "payment_failed",
  "subscription_expiring",
  "weekly_summary",
  "file_uploaded",
] as const;
export type NotificationType = (typeof NOTIFICATION_TYPES)[number];

export const DEFAULT_NOTIFICATION_PREFERENCES: Readonly<
  Record<NotificationType, Readonly<Record<NotificationChannel, boolean>>>
> = {
  member_invited: { email: true, in_app: true, push: false },
  payment_failed: { email: true, in_app: true, push: true },
  subscription_expiring: { email: true, in_app: true, push: false },
  weekly_summary: { email: true, in_app: false, push: false },
  file_uploaded: { email: false, in_app: true, push: false },
};

// Mirrors backend/product.config.py's MODULES and branding constants — keep
// both in sync. Module keys mirror ModuleName in backend/app/core/modules.py
// (camelCased for TS convention).
export interface ProductConfig {
  name: string;
  primaryColor: string;
  logoUrl: string;
  supportEmail: string;
  modules: {
    billing: boolean;
    notifications: boolean;
    fileStorage: boolean;
    featureFlags: boolean;
    gdpr: boolean;
    admin: boolean;
    oauth: boolean;
    totp2fa: boolean;
    multiOrg: boolean;
    usageMetering: boolean;
    outboundWebhooks: boolean;
  };
  // Which @forge/i18n locales this product exposes to users -- a subset of
  // (or equal to) @forge/i18n's own SUPPORTED_LOCALES. Keep in sync with
  // backend/product/config.py's SUPPORTED_LOCALES / DEFAULT_LOCALE.
  locales: {
    supported: readonly string[];
    default: string;
  };
}
