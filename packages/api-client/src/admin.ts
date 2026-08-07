import { client } from "./generated/client.gen";
import type { ErrorBody, Meta, MemberRole, UserPublic } from "./generated";

/**
 * /api/v1/admin/* is deliberately excluded from the OpenAPI schema (see
 * backend's app.modules.admin.router docstring), so there's no generated SDK
 * for it -- these are hand-written wrappers around the same `client` the
 * generated SDK functions use (see generated/sdk.gen.ts), so they still get
 * the shared baseUrl, credentials, auth-token injection, and the 401
 * refresh-and-retry interceptor for free. `security` must be passed
 * explicitly on every call below -- that's what triggers the client's
 * `auth()` callback (see generated/client/client.gen.ts's beforeRequest).
 *
 * `throwOnError: true` is baked into every call (rather than left for each
 * caller to pass, the way generated SDK call sites do) so every wrapper
 * always resolves to `{ data: Envelope<T> }` and rejects on a non-2xx
 * response -- one consistent shape for every admin page's try/catch, with
 * no risk of a call site forgetting the flag and silently getting back an
 * `error`-shaped envelope instead of a caught exception.
 */
const BEARER = [{ scheme: "bearer" as const, type: "http" as const }];
const THROW = { security: BEARER, throwOnError: true as const };

export interface Envelope<T> {
  data: T | null;
  meta: Meta;
}

// ---- T-122: per-org feature-flag overrides -----------------------------------

export interface AdminFeatureFlagState {
  resolved: boolean;
  override: boolean | null;
}

export interface AdminFeatureFlagsResponse {
  flags: Record<string, AdminFeatureFlagState>;
}

export function getAdminOrgFeatureFlags(orgId: string) {
  return client.get<Envelope<AdminFeatureFlagsResponse>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs/{org_id}/feature-flags",
    path: { org_id: orgId },
  });
}

export function setAdminOrgFeatureFlag(orgId: string, flag: string, enabled: boolean) {
  return client.put<Envelope<null>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs/{org_id}/feature-flags/{flag}",
    path: { org_id: orgId, flag },
    body: { enabled },
  });
}

export function deleteAdminOrgFeatureFlag(orgId: string, flag: string) {
  return client.delete<Envelope<null>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs/{org_id}/feature-flags/{flag}",
    path: { org_id: orgId, flag },
  });
}

// ---- T-141: org/user directory + plan override -------------------------------

export interface AdminOrgSummary {
  id: string;
  name: string;
  slug: string;
  plan_slug: string | null;
  subscription_status: string | null;
  created_at: string;
}

export interface AdminSubscriptionDetail {
  plan_slug: string;
  status: string;
  billing_interval: string | null;
  free_override: boolean;
  trial_end: string | null;
  current_period_end: string;
  override_expires_at: string | null;
}

export interface AdminOrgDetail {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  member_count: number;
  file_count: number | null;
  last_activity_at: string | null;
  subscription: AdminSubscriptionDetail | null;
}

export interface AdminUserSummary {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string;
}

export interface AdminMembershipSummary {
  org_id: string;
  org_name: string;
  org_slug: string;
  role: MemberRole;
}

export interface AdminSessionSummary {
  id: string;
  device_label: string | null;
  ip_address: string;
  last_seen_at: string;
  expires_at: string;
}

export interface AdminAuditEntry {
  action: string;
  resource_type: string;
  resource_id: string | null;
  ts: string;
}

export interface AdminAuditSummary {
  total_count: number;
  recent_actions: AdminAuditEntry[];
}

export interface AdminUserDetail {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_superadmin: boolean;
  created_at: string;
  memberships: AdminMembershipSummary[];
  sessions: AdminSessionSummary[];
  audit_summary: AdminAuditSummary;
}

export interface ListAdminOrgsQuery {
  after?: string | null;
  limit?: number | undefined;
  q?: string | undefined;
  plan?: string | undefined;
  status?: string | undefined;
}

export function listAdminOrgs(query: ListAdminOrgsQuery = {}) {
  return client.get<Envelope<AdminOrgSummary[]>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs",
    query: query as Record<string, unknown>,
  });
}

export function getAdminOrgDetail(orgId: string) {
  return client.get<Envelope<AdminOrgDetail>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs/{org_id}",
    path: { org_id: orgId },
  });
}

export interface ListAdminUsersQuery {
  after?: string | null;
  limit?: number | undefined;
  q?: string | undefined;
  active?: boolean | undefined;
}

export function listAdminUsers(query: ListAdminUsersQuery = {}) {
  return client.get<Envelope<AdminUserSummary[]>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/users",
    query: query as Record<string, unknown>,
  });
}

export function getAdminUserDetail(userId: string) {
  return client.get<Envelope<AdminUserDetail>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/users/{user_id}",
    path: { user_id: userId },
  });
}

export interface AdminSetOrgPlanRequest {
  plan_slug: string;
  billing_interval?: "monthly" | "yearly" | null;
  free_override?: boolean;
  expires_at?: string | null;
}

export function setAdminOrgPlan(orgId: string, body: AdminSetOrgPlanRequest) {
  return client.put<Envelope<null>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs/{org_id}/plan",
    path: { org_id: orgId },
    body,
  });
}

export function extendAdminOrgTrial(orgId: string, days: number) {
  return client.post<Envelope<null>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs/{org_id}/extend-trial",
    path: { org_id: orgId },
    body: { days },
  });
}

export function issueAdminOrgCompCredit(orgId: string, amountCents: number, reason: string) {
  return client.post<Envelope<null>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/orgs/{org_id}/comp-credit",
    path: { org_id: orgId },
    body: { amount_cents: amountCents, reason },
  });
}

// ---- T-142: user impersonation -------------------------------------------------

export interface AdminImpersonateRequest {
  reason: string;
  duration_minutes: number;
}

export interface AdminImpersonateResponse {
  access_token: string;
  expires_at: string;
  user: UserPublic;
}

export function impersonateAdminUser(userId: string, body: AdminImpersonateRequest) {
  return client.post<Envelope<AdminImpersonateResponse>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/users/{user_id}/impersonate",
    path: { user_id: userId },
    body,
  });
}

// ---- T-261: mobile impersonation handoff ---------------------------------------

export interface AdminMobileHandoffResponse {
  deep_link: string;
  expires_at: string;
}

/**
 * Turns an impersonation session into a two-minute deep link for the
 * superadmin's phone.
 *
 * The token is passed in and set as an explicit header instead of going through
 * `THROW`'s `security: BEARER`: that path resolves the *module's* access token
 * (client.ts's `accessToken`), which on this screen is still the superadmin's
 * own. The whole point of the admin-side handoff flow is that the browser never
 * adopts the impersonation session it is handing over -- the operator stays
 * themselves on web -- so the one call that has to be made *as* the impersonated
 * session has to carry that token by hand.
 */
export function createAdminMobileHandoff(impersonationToken: string) {
  return client.post<Envelope<AdminMobileHandoffResponse>, ErrorBody, true>({
    throwOnError: true,
    url: "/api/v1/admin/impersonate/mobile-handoff",
    headers: { Authorization: `Bearer ${impersonationToken}` },
  });
}

/**
 * Revokes an impersonation session the browser started but never adopted.
 *
 * Same explicit-header reason as createAdminMobileHandoff above, and the same
 * endpoint the phone and the web banner both use to end a session -- one jti,
 * one revocation path. This is what stops "create a link, change your mind"
 * from leaving a live session nobody is watching for up to an hour: the
 * handoff dialog is the only place holding that token, so if it does not spend
 * it here, nothing can.
 */
export function endAdminImpersonationSession(impersonationToken: string) {
  return client.post<Envelope<null>, ErrorBody, true>({
    throwOnError: true,
    url: "/api/v1/admin/impersonate/end",
    headers: { Authorization: `Bearer ${impersonationToken}` },
  });
}

// ---- T-143: dashboard + global audit feed --------------------------------------

export interface AdminPaymentFailureEntry {
  org_id: string;
  org_name: string;
  subscription_id: string | null;
  ts: string;
}

export interface AdminDashboardStats {
  org_count: number;
  user_count: number;
  mrr_cents: number | null;
  recent_signups: AdminUserSummary[];
  recent_payment_failures: AdminPaymentFailureEntry[];
}

export function getAdminDashboard() {
  return client.get<Envelope<AdminDashboardStats>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/dashboard",
  });
}

export interface AdminAuditFeedEntry {
  id: string;
  ts: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  severity: string;
  actor_id: string | null;
  actor_email: string | null;
  org_id: string | null;
  org_name: string | null;
  impersonator_id: string | null;
}

export interface ListAdminAuditFeedQuery {
  after?: string | null;
  limit?: number | undefined;
  actor?: string | undefined;
  action?: string | undefined;
  org?: string | undefined;
  since?: string | undefined;
  until?: string | undefined;
}

export function listAdminAuditFeed(query: ListAdminAuditFeedQuery = {}) {
  return client.get<Envelope<AdminAuditFeedEntry[]>, ErrorBody, true>({
    ...THROW,
    url: "/api/v1/admin/audit",
    query: query as Record<string, unknown>,
  });
}
