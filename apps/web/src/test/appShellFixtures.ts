import type {
  ConfigResponse,
  ConsentRecordPublic,
  DataExportJobPublic,
  InvitationPreview,
  InvitationPublic,
  MembershipPublic,
  NotificationPublic,
  OrganizationPublic,
  PlanPublic,
  SessionPublic,
  StoredFilePublic,
  SubscriptionPublic,
  UserPublic,
} from "@forge/api-client";

// Canonical fixtures + envelope helpers for the whole frontend test suite --
// the MSW default handlers in ./handlers use these as their happy-path
// response bodies, and individual test files reuse the same shapes when
// overriding a handler via `server.use(...)`, instead of each file inventing
// its own. Also still covers the AppLayout integration tests' need for a
// fresh module instance per scenario (see useAuth/useOrg's module-scoped
// bootstrap caching) -- those files render the real, unmocked bootstrap
// chain and only the SDK calls underneath are mocked, so splitting each
// scenario into its own file is what gives each one a fresh bootstrap.

export function makeUser(
  suffix: number,
  overrides: Partial<UserPublic> = {},
): UserPublic {
  return {
    id: `11111111-1111-1111-1111-${String(suffix).padStart(12, "0")}`,
    email: "jane@example.com",
    full_name: "Jane Doe",
    avatar_url: null,
    locale: "en",
    email_verified_at: "2026-01-01T00:00:00Z",
    totp_enabled: false,
    is_superadmin: false,
    ...overrides,
  };
}

export const fixtureUser: UserPublic = makeUser(1);

export const fixtureOrg: OrganizationPublic = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Acme Inc",
  slug: "acme-inc",
  logo_url: null,
  created_at: "2026-01-01T00:00:00Z",
};

export function makeMembership(
  user: UserPublic,
  role: MembershipPublic["role"],
): MembershipPublic {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    user_id: user.id,
    org_id: fixtureOrg.id,
    role,
    invited_by: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

export function makeNotification(
  suffix: number,
  overrides: Partial<NotificationPublic> = {},
): NotificationPublic {
  return {
    id: `66666666-6666-6666-6666-${String(suffix).padStart(12, "0")}`,
    type: "member_invited",
    title: "Someone joined your organization",
    body: "Jane Doe accepted your invitation.",
    action_url: null,
    channels: ["in_app"],
    read_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export const fixtureNotification: NotificationPublic = makeNotification(1);

export const fixtureMembership: MembershipPublic = makeMembership(
  fixtureUser,
  "owner",
);

export const fixturePlan: PlanPublic = {
  id: "55555555-5555-5555-5555-555555555555",
  name: "Free",
  slug: "free",
  price_monthly: 0,
  price_yearly: 0,
  features: {},
};

export const fixtureSubscription: SubscriptionPublic = {
  id: "77777777-7777-7777-7777-777777777777",
  plan_id: fixturePlan.id,
  status: "active",
  current_period_start: "2026-01-01T00:00:00Z",
  current_period_end: "2026-02-01T00:00:00Z",
  cancel_at_period_end: false,
  trial_end: null,
};

export const fixtureSession: SessionPublic = {
  id: "44444444-4444-4444-4444-444444444444",
  device_label: "Chrome on macOS",
  ip_address: "127.0.0.1",
  user_agent: "test-agent",
  last_seen_at: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2026-02-01T00:00:00Z",
};

export const fixtureInvitationPreview: InvitationPreview = {
  org_id: fixtureOrg.id,
  org_name: fixtureOrg.name,
  email: "jane@example.com",
  role: "member",
  expires_at: "2026-12-31T00:00:00Z",
};

export function makeInvitation(
  suffix: number,
  overrides: Partial<InvitationPublic> = {},
): InvitationPublic {
  return {
    id: `99999999-9999-9999-9999-${String(suffix).padStart(12, "0")}`,
    org_id: fixtureOrg.id,
    email: `invitee-${String(suffix)}@example.com`,
    role: "member",
    invited_by: fixtureUser.id,
    // Far enough out that "is this expired?" never depends on when the
    // suite runs -- the expired case passes an explicit past date instead.
    expires_at: "2099-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export const fixtureExportJob: DataExportJobPublic = {
  id: "88888888-8888-8888-8888-888888888888",
  status: "pending",
  download_url: null,
  expires_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

// Mirrors backend/product/config.py's defaults -- every module on except
// usage_metering/outbound_webhooks (opt-in, off by default).
export const fixtureConfig: ConfigResponse = {
  app_name: "ForgeSaaS",
  primary_color: "#7c6ff7",
  logo_url: "/logo.svg",
  support_email: "support@example.com",
  modules: {
    billing: true,
    notifications: true,
    file_storage: true,
    feature_flags: true,
    gdpr: true,
    admin: true,
    oauth: true,
    totp_2fa: true,
    multi_org: true,
    usage_metering: false,
    outbound_webhooks: false,
  },
  supported_locales: ["en", "fi", "de", "es", "fr", "ar"],
  default_locale: "en",
};

export const fixtureConsent: ConsentRecordPublic = {
  id: "99999999-9999-9999-9999-999999999999",
  consent_type: "marketing",
  granted: true,
  version: "1.0.0",
  ts: "2026-01-01T00:00:00Z",
};

export function makeStoredFile(
  suffix: number,
  overrides: Partial<StoredFilePublic> = {},
): StoredFilePublic {
  return {
    id: `aaaaaaaa-aaaa-aaaa-aaaa-${String(suffix).padStart(12, "0")}`,
    bucket: "default",
    filename: `file-${String(suffix)}.txt`,
    content_type: "text/plain",
    size_bytes: 1024 * suffix,
    status: "ready",
    virus_scan_result: null,
    uploaded_by: fixtureUser.id,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export const fixtureStoredFile: StoredFilePublic = makeStoredFile(1);

// The plain `{ data, error, meta }` shape the backend actually returns --
// used directly as an MSW JSON response body. (Distinct from the
// `{ data: <this> }` shape the generated SDK functions resolve to; tests
// that mock those functions directly, rather than the network layer, wrap
// this once more themselves.)
export function envelope<T>(data: T): {
  data: T;
  error: null;
  meta: { request_id: string };
} {
  return { data, error: null, meta: { request_id: "req-1" } };
}

export function errorEnvelope(
  message: string,
  code = "internal_error",
): {
  data: null;
  error: { code: string; message: string };
  meta: { request_id: string };
} {
  return {
    data: null,
    error: { code, message },
    meta: { request_id: "req-1" },
  };
}
