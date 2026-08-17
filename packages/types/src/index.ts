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
  // Sent by the virus scanner (backend/app/workers/jobs/virus_scan.py) since
  // T-102. It was missing from this list, so the settings page never offered
  // a row for it and the one notification type most worth muting or routing
  // to push had no controls at all.
  "file_infected",
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
  file_infected: { email: true, in_app: true, push: true },
};

/**
 * Channels the user cannot switch off, mirroring REQUIRED_CHANNELS in
 * backend/app/modules/notifications/preferences.py (T-234). Both of these
 * carry the dunning sequence — "we couldn't charge your card, you are about
 * to lose access" — which is a service message, not something the product
 * chose to send.
 *
 * The settings page renders these boxes checked and disabled rather than
 * letting them look live: the backend forces them back on when the matrix is
 * saved, so a checkbox that appeared to work would simply be lying.
 */
export const REQUIRED_NOTIFICATION_CHANNELS: Readonly<
  Partial<Record<NotificationType, readonly NotificationChannel[]>>
> = {
  payment_failed: ["email"],
  subscription_expiring: ["email"],
};

export function isNotificationChannelRequired(
  type: NotificationType,
  channel: NotificationChannel,
): boolean {
  return REQUIRED_NOTIFICATION_CHANNELS[type]?.includes(channel) ?? false;
}

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
    // T-290 (Phase 4). `marketing` is the odd one out: it gates the
    // apps/marketing *build* rather than a router, so the app reads it only
    // to decide whether a link off to the marketing site is worth rendering.
    marketing: boolean;
    publicApi: boolean;
    enterpriseSso: boolean;
  };
  // Per-locale toggle, mirroring `modules` above (and
  // backend/product/config.py's EnabledLocales/LocaleConfig, T-231) -- each
  // key is a @forge/i18n locale this product may expose to users, `default`
  // names which one loads before any detection runs. Keep in sync with
  // backend/product/config.py's LOCALES / DEFAULT_LOCALE.
  locales: {
    en: boolean;
    fi: boolean;
    de: boolean;
    es: boolean;
    fr: boolean;
    ar: boolean;
    default: string;
  };
}
