/**
 * T-261: the mobile half of impersonation handoff -- redeeming a deep link
 * from the admin console, and everything that has to stay true for as long as
 * this device is wearing someone else's identity.
 *
 * Three rules shape this file, and each one is a rule because the alternative
 * is a leak rather than a bug:
 *
 * 1. **The refresh token is deleted, not kept.** An impersonation session has
 *    no refresh token of its own (backend's AdminImpersonationService never
 *    mints one), so if a previous sign-in left one behind, api.ts's 401
 *    interceptor would silently refresh *that* session back into place the
 *    moment the impersonation token expired -- turning "the session ended"
 *    into "you are now someone else again". Clearing it makes expiry mean
 *    what it says: back to the login screen.
 *
 * 2. **The flag never outlives the session it describes.**
 *    `clearImpersonationOnLaunch()` runs before anything reads the stored
 *    session, so an app killed mid-impersonation comes back signed out rather
 *    than quietly still impersonating -- the one state where an operator could
 *    forget which identity they are in. The other half of that rule lives in
 *    ./api: `clearStoredTokens()` removes the flag too, so a logout or an
 *    expiry can't leave one behind for the *next* launch to find and act on
 *    (it would wipe the operator's own session). And when the session expires
 *    on its own, the listener at the bottom of this file drops the banner
 *    rather than leaving it counting down "00:00" over the login screen.
 *
 * 3. **Push registration is not touched.** Nothing here calls
 *    registerPushToken(); apps/mobile/app/(app)/_layout.tsx skips it while
 *    this store says impersonation is on, so the impersonated user's
 *    notifications keep going to *their* devices and never to the
 *    superadmin's. (The backend suppresses pushes raised *by* an impersonated
 *    request separately -- see T-260's NotificationService.send.)
 */
import { useSyncExternalStore } from "react";

import * as SecureStore from "expo-secure-store";

import {
  ACCESS_TOKEN_KEY,
  IMPERSONATION_SESSION_KEY,
  REFRESH_TOKEN_KEY,
  clearStoredTokens,
  client,
  hydrateAccessTokenCache,
  onSessionExpired,
} from "./api";
import { clearSession } from "./auth";

// Re-exported so callers (and this module's tests) have one import for the
// whole impersonation surface; it is *defined* in ./api because
// clearStoredTokens() has to be able to remove it. See its comment there.
export { IMPERSONATION_SESSION_KEY };

const PREVIEW_PATH = "/api/v1/admin/impersonate/mobile-handoff/preview";
const EXCHANGE_PATH = "/api/v1/admin/impersonate/mobile-exchange";
const CANCEL_PATH = "/api/v1/admin/impersonate/mobile-cancel";
const END_PATH = "/api/v1/admin/impersonate/end";

// /api/v1/admin/* is excluded from the OpenAPI schema (see the backend
// router's docstring), so there is no generated SDK for these four calls --
// same situation packages/api-client/src/admin.ts is in on web, solved the
// same way, against this app's own Client rather than the browser singleton.
interface Envelope<T> {
  data: T | null;
}

export interface HandoffPreview {
  user_email: string;
  reason: string;
  session_expires_at: string;
}

interface ExchangeResponse {
  access_token: string;
  session_expires_at: string;
  user_email: string;
  reason: string;
}

export interface ImpersonationState {
  email: string;
  /** Epoch ms the session's access token expires at -- the banner counts down
   * against this rather than against a duration, so backgrounding the app
   * can't stretch it. */
  expiresAt: number;
  reason: string;
}

let state: ImpersonationState | null = null;
const listeners = new Set<() => void>();

function setState(next: ImpersonationState | null): void {
  state = next;
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getImpersonationSnapshot(): ImpersonationState | null {
  return state;
}

export function useImpersonation(): ImpersonationState | null {
  return useSyncExternalStore(subscribe, getImpersonationSnapshot);
}

export async function previewHandoff(token: string): Promise<HandoffPreview> {
  // POST for a read: the exchange token is a live credential, and a path
  // parameter would leave it in every proxy access log and in Sentry's
  // `request.url`. The backend leg is shaped the same way for the same
  // reason -- see admin/router.py's preview_mobile_handoff.
  const { data } = await client.post<Envelope<HandoffPreview>, unknown, true>({
    url: PREVIEW_PATH,
    body: { token },
    throwOnError: true,
  });
  if (!data.data) {
    throw new Error("Handoff preview response was empty.");
  }
  return data.data;
}

/**
 * Redeems the exchange token and adopts the session on this device.
 *
 * The caller is expected to re-bootstrap the session afterwards (see
 * apps/mobile/app/impersonate.tsx) -- this function owns the credential, not
 * the navigation.
 */
export async function acceptHandoff(
  token: string,
): Promise<ImpersonationState> {
  const { data } = await client.post<Envelope<ExchangeResponse>, unknown, true>(
    {
      url: EXCHANGE_PATH,
      body: { token },
      throwOnError: true,
    },
  );
  if (!data.data) {
    throw new Error("Handoff exchange response was empty.");
  }
  const session: ImpersonationState = {
    email: data.data.user_email,
    expiresAt: new Date(data.data.session_expires_at).getTime(),
    reason: data.data.reason,
  };
  // Order matters: the refresh token goes first, so a crash between these
  // writes can never leave the device holding an impersonation access token
  // *and* a way to refresh back into the previous account.
  await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
  await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, data.data.access_token);
  await SecureStore.setItemAsync(
    IMPERSONATION_SESSION_KEY,
    JSON.stringify(session),
  );
  // api.ts's request interceptor reads an in-memory copy, not SecureStore, so
  // without this the very next call would go out unauthenticated (or worse,
  // with whatever the previous session left cached).
  await hydrateAccessTokenCache();
  setState(session);
  return session;
}

/** Declines a handoff, burning the link server-side rather than leaving it
 * readable for the rest of its two minutes. */
export async function declineHandoff(token: string): Promise<void> {
  await client.post<Envelope<null>, unknown, true>({
    url: CANCEL_PATH,
    body: { token },
    throwOnError: true,
  });
}

/**
 * Ends the session from this device: revokes it server-side (which revokes it
 * everywhere -- one jti), then wipes the device.
 *
 * The wipe happens even if the server call fails. A network error must not
 * leave an operator stuck inside someone else's identity with no way out; the
 * token expires on its own within the hour either way, and the audit trail
 * already records that the session started.
 */
export async function endImpersonation(): Promise<void> {
  try {
    await client.post<Envelope<null>, unknown, true>({
      url: END_PATH,
      body: { channel: "mobile" },
      throwOnError: true,
    });
  } finally {
    // clearStoredTokens() removes the impersonation flag along with the
    // tokens, and clearSession() drops the impersonated user from
    // useSession() -- without that second step the app would keep rendering
    // that identity, banner-less, on the next navigation.
    await clearStoredTokens();
    clearSession();
    setState(null);
  }
}

/**
 * Cold-start guard. Returns whether an impersonation session was found and
 * discarded, so the caller can decide where to send the user.
 *
 * Called from the root layout before bootstrapSession(), which is what makes
 * it a guard rather than a cleanup: by the time anything hydrates the stored
 * access token, there is no impersonation token left to hydrate.
 */
export async function clearImpersonationOnLaunch(): Promise<boolean> {
  const stored = await SecureStore.getItemAsync(IMPERSONATION_SESSION_KEY);
  if (stored === null) {
    return false;
  }
  await clearStoredTokens();
  clearSession();
  setState(null);
  return true;
}

// An impersonation session has no refresh token, so the first call made after
// its access token expires takes api.ts's "refresh failed, the session is
// over" path. Without this, the red banner would keep counting down "00:00
// left" over the login screen the operator was just bounced to, and -- worse
// before clearStoredTokens() learned to remove the flag -- the leftover key
// would be waiting for the next cold start. Registered at module load,
// alongside auth.ts's own listener, so it fires from wherever the expiry is
// noticed.
onSessionExpired(() => {
  setState(null);
});
