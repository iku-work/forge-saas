import { http, HttpResponse } from "msw";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  errorEnvelope,
  fixtureConfig,
  fixtureConsent,
  fixtureExportJob,
  fixtureInvitationPreview,
  fixtureMembership,
  fixtureNotification,
  fixtureOrg,
  fixturePlan,
  fixtureStoredFile,
  fixtureSubscription,
  fixtureSupportContext,
  fixtureUser,
} from "./appShellFixtures";

// Closes immediately after one keepalive comment rather than staying open --
// AppLayout-level tests that don't care about live notifications still mount
// NotificationBell (whenever the notifications module is on, same as
// production), and dozens of test files mounting it in parallel each holding
// a genuinely open MSW-mocked connection was enough concurrent load to starve
// unrelated tests' default timeouts. lib/sse.ts schedules one reconnect 1s
// after this closes, but RTL's afterEach unmount (and this module's
// ref-counted unsubscribe) cancels that pending timer before it ever fires
// in practice, since a test's assertions finish well under a second.
function shortNotificationStream(): HttpResponse<string> {
  return new HttpResponse(": ping\n\n", {
    headers: { "Content-Type": "text/event-stream" },
  });
}

// Default, happy-path stand-ins for every endpoint the generated SDK can
// call, keyed to the same base URL the real client resolves
// (getApiBaseUrl() honours VITE_API_URL; tests never set it, so this is
// "http://localhost:8000"). A component under test that hits an endpoint
// nobody thought to mock gets a realistic envelope instead of a hung
// request -- tests that care about a specific response override the
// relevant handler with `server.use(...)`.
//
// /auth/refresh defaults to "no session" (401) rather than a successful
// envelope: most page-level tests render unauthenticated by default (the
// login/register/forgot-password/etc. pages, and the router's auth-guard
// redirect tests), and useAuth's bootstrap calls refresh unconditionally on
// mount. Tests that need an authenticated session override it explicitly.
const API = getApiBaseUrl();

export const handlers = [
  http.post(`${API}/api/v1/auth/register`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/login`, () =>
    HttpResponse.json(
      envelope({ access_token: "default-access-token", user: fixtureUser }),
    ),
  ),
  http.post(`${API}/api/v1/auth/refresh`, () =>
    HttpResponse.json(
      errorEnvelope("No active session.", "authentication_failed"),
      {
        status: 401,
      },
    ),
  ),
  http.post(`${API}/api/v1/auth/logout`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/logout-all`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/verify-email`, () =>
    HttpResponse.json(envelope(fixtureUser)),
  ),
  http.post(`${API}/api/v1/auth/forgot-password`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/reset-password`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/magic-link`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/magic-link/consume`, () =>
    HttpResponse.json(
      envelope({ access_token: "default-access-token", user: fixtureUser }),
    ),
  ),
  http.get(`${API}/api/v1/auth/me`, () =>
    HttpResponse.json(envelope(fixtureUser)),
  ),
  http.get(`${API}/api/v1/auth/sessions`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.delete(`${API}/api/v1/auth/sessions/:sessionId`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/change-password`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.get(`${API}/api/v1/auth/oauth/accounts`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.get(`${API}/api/v1/auth/oauth/:provider`, () =>
    HttpResponse.redirect("https://oauth.example.test/authorize", 302),
  ),
  http.get(`${API}/api/v1/auth/oauth/:provider/callback`, () =>
    HttpResponse.redirect("/", 302),
  ),
  http.post(`${API}/api/v1/auth/oauth/:provider/unlink`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/2fa/enable`, () =>
    HttpResponse.json(
      envelope({
        provisioning_uri: "otpauth://totp/ForgeSaaS:jane@example.com",
        secret: "SECRET",
      }),
    ),
  ),
  http.post(`${API}/api/v1/auth/2fa/verify`, () =>
    HttpResponse.json(envelope({ backup_codes: ["aaaa-1111", "bbbb-2222"] })),
  ),
  http.post(`${API}/api/v1/auth/2fa/disable`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/2fa/login`, () =>
    HttpResponse.json(
      envelope({ access_token: "default-access-token", user: fixtureUser }),
    ),
  ),
  http.post(`${API}/api/v1/auth/webauthn/register/options`, () =>
    HttpResponse.json(
      envelope({
        options: {
          rp: { id: "localhost", name: "ForgeSaaS" },
          user: { id: "dXNlci1pZA", name: "jane@example.com", displayName: "jane@example.com" },
          challenge: "Y2hhbGxlbmdl",
          pubKeyCredParams: [{ type: "public-key", alg: -7 }],
        },
        challenge_token: "register-challenge-token",
      }),
    ),
  ),
  http.post(`${API}/api/v1/auth/webauthn/register`, () =>
    HttpResponse.json(
      envelope({
        id: "cred-1",
        nickname: null,
        aaguid: "00000000-0000-0000-0000-000000000000",
        created_at: "2026-01-01T00:00:00Z",
        last_used_at: null,
      }),
    ),
  ),
  http.get(`${API}/api/v1/auth/webauthn/credentials`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.delete(`${API}/api/v1/auth/webauthn/credentials/:credentialId`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.post(`${API}/api/v1/auth/webauthn/login/options`, () =>
    HttpResponse.json(
      envelope({
        options: {
          challenge: "Y2hhbGxlbmdl",
          allowCredentials: [],
        },
        challenge_token: "login-challenge-token",
      }),
    ),
  ),
  http.post(`${API}/api/v1/auth/webauthn/login`, () =>
    HttpResponse.json(
      envelope({ access_token: "default-access-token", user: fixtureUser }),
    ),
  ),
  http.get(`${API}/api/v1/orgs`, () => HttpResponse.json(envelope([]))),
  http.post(`${API}/api/v1/orgs`, () =>
    HttpResponse.json(envelope(fixtureOrg)),
  ),
  http.get(`${API}/api/v1/orgs/:orgId`, () =>
    HttpResponse.json(envelope(fixtureOrg)),
  ),
  http.patch(`${API}/api/v1/orgs/:orgId`, () =>
    HttpResponse.json(envelope(fixtureOrg)),
  ),
  http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.get(`${API}/api/v1/orgs/:orgId/feature-flags`, () =>
    HttpResponse.json(envelope({ flags: { advanced_reports: false } })),
  ),
  http.patch(`${API}/api/v1/orgs/:orgId/members/:membershipId`, () =>
    HttpResponse.json(envelope(fixtureMembership)),
  ),
  http.delete(`${API}/api/v1/orgs/:orgId/members/:membershipId`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.post(`${API}/api/v1/orgs/:orgId/invitations`, () =>
    HttpResponse.json(envelope(null)),
  ),
  http.get(`${API}/api/v1/invitations/:token`, () =>
    HttpResponse.json(envelope(fixtureInvitationPreview)),
  ),
  http.post(`${API}/api/v1/invitations/:token/accept`, () =>
    HttpResponse.json(envelope(fixtureMembership)),
  ),
  http.post(`${API}/api/v1/billing/checkout-session`, () =>
    HttpResponse.json(
      envelope({ url: "https://billing.example.test/checkout" }),
    ),
  ),
  http.post(`${API}/api/v1/billing/portal-session`, () =>
    HttpResponse.json(envelope({ url: "https://billing.example.test/portal" })),
  ),
  http.get(`${API}/api/v1/billing/subscription`, () =>
    HttpResponse.json(envelope(fixtureSubscription)),
  ),
  http.get(`${API}/api/v1/billing/plans`, () =>
    HttpResponse.json(envelope([fixturePlan])),
  ),
  http.get(`${API}/api/v1/billing/invoices`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.get(`${API}/api/v1/billing/usage`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.post(`${API}/api/v1/billing/webhooks/stripe`, () =>
    HttpResponse.json({ received: true }),
  ),
  http.get(`${API}/api/v1/me/export`, () =>
    HttpResponse.json(envelope({ job_id: fixtureExportJob.id }), {
      status: 202,
    }),
  ),
  http.get(`${API}/api/v1/me/export/:jobId`, () =>
    HttpResponse.json(envelope(fixtureExportJob)),
  ),
  http.delete(`${API}/api/v1/me`, () =>
    HttpResponse.json(envelope(null), { status: 202 }),
  ),
  http.get(`${API}/api/v1/me/consents`, () => HttpResponse.json(envelope([]))),
  http.post(`${API}/api/v1/me/consents`, () =>
    HttpResponse.json(envelope(fixtureConsent)),
  ),
  http.patch(`${API}/api/v1/me/notification-preferences`, () =>
    HttpResponse.json(envelope({ preferences: {} })),
  ),
  http.get(`${API}/api/v1/notifications/stream`, shortNotificationStream),
  http.get(`${API}/api/v1/notifications`, () =>
    HttpResponse.json(envelope([])),
  ),
  http.get(`${API}/api/v1/notifications/unread-count`, () =>
    HttpResponse.json(envelope({ count: 0 })),
  ),
  http.patch(`${API}/api/v1/notifications/:notificationId/read`, () =>
    HttpResponse.json(envelope(fixtureNotification)),
  ),
  http.post(`${API}/api/v1/notifications/read-all`, () =>
    HttpResponse.json(envelope({ updated: 0 })),
  ),
  http.get(`${API}/api/v1/config`, () =>
    HttpResponse.json(envelope(fixtureConfig)),
  ),
  http.get(`${API}/api/v1/me/support-context`, () =>
    HttpResponse.json(envelope(fixtureSupportContext)),
  ),
  http.get(`${API}/health`, () =>
    HttpResponse.json({ status: "ok", version: "0.1.0" }),
  ),
  http.get(`${API}/health/live`, () => HttpResponse.json({ status: "ok" })),
  http.get(`${API}/health/ready`, () => HttpResponse.json({ status: "ok" })),
  http.get(`${API}/api/v1/files`, () =>
    HttpResponse.json(envelope([fixtureStoredFile])),
  ),
  http.post(`${API}/api/v1/files/presign`, () =>
    HttpResponse.json(
      envelope({
        file_id: fixtureStoredFile.id,
        upload_url: "https://s3.example.test/upload",
        fields: {},
        key: "files/test",
        expires_at: "2026-12-31T00:00:00Z",
      }),
    ),
  ),
  http.post(`${API}/api/v1/files/:id/confirm`, () =>
    HttpResponse.json(envelope(fixtureStoredFile)),
  ),
  http.get(`${API}/api/v1/files/:id/download`, () =>
    HttpResponse.json(
      envelope({
        url: "https://s3.example.test/download",
        expires_at: "2026-12-31T00:00:00Z",
      }),
    ),
  ),
  http.delete(`${API}/api/v1/files/:id`, () =>
    HttpResponse.json(envelope(null)),
  ),
];
