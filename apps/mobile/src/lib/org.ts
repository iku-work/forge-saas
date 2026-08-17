/**
 * Minimal current-org resolution for mobile. T-105's notifications screen is
 * mobile's first authenticated call to an org-scoped endpoint
 * (GET /api/v1/notifications requires X-Org-ID, see app.core.dependencies.
 * get_current_org) -- Phase 1's mobile build never needed an org context, so
 * none exists yet (no org switcher, no multi-org UI).
 *
 * Until mobile gets a real switcher, this resolves to the caller's first
 * organization and pins it via @forge/api-client's setCurrentOrgId -- the
 * same store apps/web's useOrg() writes to, and the same
 * orgIdRequestInterceptor (wired onto this app's own client in ./api.ts)
 * reads from on every outgoing request.
 */
import { listMyOrgsApiV1OrgsGet, setCurrentOrgId } from "@forge/api-client";

import { client } from "./api";

let bootstrapPromise: Promise<void> | null = null;

export function ensureCurrentOrg(): Promise<void> {
  bootstrapPromise ??= (async () => {
    const { data: envelope } = await listMyOrgsApiV1OrgsGet({
      client,
      throwOnError: true,
    });
    const firstOrg = envelope.data?.[0];
    if (firstOrg) {
      setCurrentOrgId(firstOrg.id);
    }
  })();
  return bootstrapPromise;
}

/**
 * Forgets the pinned org. Called from lib/auth.ts whenever a session ends.
 *
 * The memoised promise above is "resolve this once per app run", which was a
 * session-lifetime assumption dressed as a process-lifetime one: nothing
 * cleared it, so after a logout and a different login `ensureCurrentOrg()`
 * returned the already-resolved promise and left the *previous* user's org
 * pinned as X-Org-ID. Every org-scoped request the new user made then carried
 * an org they are not a member of, and the backend refused it -- correctly,
 * which is why this surfaced as unexplained 403s on the notifications and
 * files screens rather than as anyone seeing another tenant's data.
 */
export function resetCurrentOrg(): void {
  bootstrapPromise = null;
  setCurrentOrgId(null);
}
