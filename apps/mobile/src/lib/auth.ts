/**
 * High-level auth actions for the mobile app (T-046). Builds on top of
 * apps/mobile/src/lib/api.ts's configured `client` and secure-store helpers:
 * this file owns the *what* (log in, log out, hand back the current
 * token), api.ts owns the *plumbing* (where tokens live, how a 401 gets
 * silently retried).
 */
import { useSyncExternalStore } from "react";

import {
  loginApiV1AuthLoginPost,
  logoutApiV1AuthLogoutPost,
  meApiV1AuthMeGet,
  registerApiV1AuthRegisterPost,
  type UserPublic,
} from "@forge/api-client";
import * as SecureStore from "expo-secure-store";

import {
  ACCESS_TOKEN_KEY,
  clearStoredTokens,
  client,
  getStoredRefreshToken,
  hydrateAccessTokenCache,
  onSessionExpired,
  setStoredTokens,
} from "./api";

// Module-scoped store (not React Context) so every useSession() call site --
// the root layout's redirect, the (app) group's guard, the invitation screen
// -- reads the same session without a provider component. Mirrors
// packages/hooks's useAuth.ts store, reimplemented here because that hook is
// wired to the web singleton client's cookie-based session, which doesn't
// exist on mobile.
interface SessionState {
  user: UserPublic | null;
  isLoading: boolean;
}

let sessionState: SessionState = { user: null, isLoading: true };
const sessionListeners = new Set<() => void>();

function setSessionState(next: Partial<SessionState>): void {
  sessionState = { ...sessionState, ...next };
  for (const listener of sessionListeners) {
    listener();
  }
}

function subscribeSession(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => {
    sessionListeners.delete(listener);
  };
}

// Exported (not just used internally by useSession()) so non-component code
// -- tests, and any future interceptor that needs to read the session
// without subscribing to it -- can read the current value without React's
// rule against calling hooks outside a component.
export function getSessionSnapshot(): SessionState {
  return sessionState;
}

/**
 * Drops the in-memory session without calling the server (T-261).
 *
 * logout() below does this as the tail of a real /auth/logout call, which is
 * wrong for the two paths that end a session some other way: ending an
 * impersonation session (its own endpoint) and discarding one at cold start.
 * Those have to clear `user` too -- otherwise useSession() keeps handing out
 * the identity whose token was just wiped, and a navigation back into the app
 * re-renders it with the impersonation banner already gone: exactly the
 * unbadged state the banner exists to prevent.
 */
export function clearSession(): void {
  setSessionState({ user: null, isLoading: false });
}

export function useSession(): SessionState {
  return useSyncExternalStore(subscribeSession, getSessionSnapshot);
}

// The response interceptor (api.ts) calls this only once a refresh-and-retry
// cycle has confirmed the refresh token itself is gone -- the session is
// genuinely over, not just mid-refresh. Subscribed once at module load so it
// fires regardless of which screen currently has a useSession() call mounted.
onSessionExpired(() => {
  setSessionState({ user: null, isLoading: false });
});

// Thrown when /auth/login responds with a 2FA challenge instead of tokens --
// mirrors packages/hooks's useAuth.ts. There is no mobile 2FA-verification
// screen yet (T-047 covers Phase-1 parity only, which has none on web
// either), so this surfaces as a regular form error.
export class TwoFactorRequiredError extends Error {
  constructor(public readonly tempToken: string) {
    super("Two-factor authentication is required to finish signing in.");
    this.name = "TwoFactorRequiredError";
  }
}

export async function login(
  email: string,
  password: string,
): Promise<UserPublic> {
  const { data: envelope } = await loginApiV1AuthLoginPost({
    client,
    body: { email, password },
    throwOnError: true,
  });
  const payload = envelope.data;
  // `pending_2fa` is optional in the generated type (mirrors the backend's
  // `= True` default), so narrowing on it wouldn't actually exclude
  // LoginResponse below -- narrow on `temp_token` instead, which is required
  // and exclusive to the challenge response.
  if (payload && "temp_token" in payload) {
    throw new TwoFactorRequiredError(payload.temp_token);
  }
  if (!payload?.refresh_token) {
    throw new Error("Login response did not include a refresh token.");
  }
  await setStoredTokens(payload.access_token, payload.refresh_token);
  setSessionState({ user: payload.user, isLoading: false });
  return payload.user;
}

export async function register(email: string, password: string): Promise<void> {
  await registerApiV1AuthRegisterPost({
    client,
    body: { email, password },
    throwOnError: true,
  });
}

export async function logout(): Promise<void> {
  const refreshToken = await getStoredRefreshToken();
  try {
    await logoutApiV1AuthLogoutPost({
      client,
      body: refreshToken ? { refresh_token: refreshToken } : undefined,
    });
  } finally {
    await clearStoredTokens();
    setSessionState({ user: null, isLoading: false });
  }
}

// Called once at app cold start (apps/mobile/app/_layout.tsx) to hydrate the
// in-memory access-token cache api.ts's request interceptor reads on every
// call, then resolves useSession()'s user from it. Returns whether a stored
// session was found at all. A stored token that turns out to be expired is
// caught here by the /me call's own 401-refresh-retry cycle, not treated as
// a separate case.
export async function bootstrapSession(): Promise<boolean> {
  const accessToken = await hydrateAccessTokenCache();
  if (!accessToken) {
    setSessionState({ user: null, isLoading: false });
    return false;
  }
  try {
    const { data: envelope } = await meApiV1AuthMeGet({
      client,
      throwOnError: true,
    });
    setSessionState({ user: envelope.data ?? null, isLoading: false });
  } catch {
    setSessionState({ user: null, isLoading: false });
  }
  return true;
}

// The interceptor's own reads never gate on biometrics -- prompting on every
// outgoing request would make the app unusable. This is for call sites that
// want an elevated check (e.g. before revealing account/session details).
export async function getAccessToken(
  options: { requireBiometric?: boolean } = {},
): Promise<string | null> {
  return SecureStore.getItemAsync(
    ACCESS_TOKEN_KEY,
    options.requireBiometric
      ? {
          requireAuthentication: true,
          authenticationPrompt: "Confirm it's you to continue",
        }
      : undefined,
  );
}
