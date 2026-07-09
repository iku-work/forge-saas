import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  getAccessToken,
  getApiBaseUrl,
  getImpersonatorId,
  loginApiV1AuthLoginPost,
  logoutApiV1AuthLogoutPost,
  meApiV1AuthMeGet,
  onSessionExpired,
  refreshApiV1AuthRefreshPost,
  registerApiV1AuthRegisterPost,
  setAccessToken,
} from "@forge/api-client";
import type { UserPublic } from "@forge/api-client";

// /api/v1/admin/* is deliberately excluded from the OpenAPI schema (see
// backend's app.modules.admin.router docstring), so there's no generated SDK
// function for it -- a raw fetch, same pattern authInterceptor.ts's
// performRefresh already uses for the same reason.
const IMPERSONATE_END_PATH = "/api/v1/admin/impersonate/end";

// Thrown when /auth/login responds with a 2FA challenge instead of tokens.
// Callers (LoginPage's TwoFactorChallenge step, T-217) catch this and trade
// tempToken for real tokens via either /auth/2fa/login or the WebAuthn
// login/options+login pair, then hand the result to completeTwoFactorLogin
// below.
export class TwoFactorRequiredError extends Error {
  constructor(public readonly tempToken: string) {
    super("Two-factor authentication is required to finish signing in.");
    this.name = "TwoFactorRequiredError";
  }
}

interface AuthState {
  user: UserPublic | null;
  isLoading: boolean;
  // T-142: true when the current access token carries an impersonator_id
  // claim -- drives the app shell's non-dismissable "Impersonating..."
  // banner. Recomputed alongside `user` on every place the access token
  // changes (login, bootstrap/refresh, logout).
  isImpersonating: boolean;
}

// Module-scoped store (not React Context) so every `useAuth()` call site --
// AppRoot, the login form, the app shell's user menu -- reads and writes the
// same session without a provider component, and so the hook file itself
// never needs JSX.
let state: AuthState = { user: null, isLoading: true, isImpersonating: false };
const listeners = new Set<() => void>();

function setState(next: Partial<AuthState>): void {
  state = { ...state, ...next };
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

function getSnapshot(): AuthState {
  return state;
}

// Shared by login()'s happy path, loadImpersonatedSession, and
// completeTwoFactorLogin -- every place that already has a real access token
// + user pair in hand and just needs to load them into session state.
function applySession(accessToken: string, user: UserPublic): void {
  setAccessToken(accessToken);
  setState({ user, isImpersonating: getImpersonatorId(accessToken) !== null });
}

// The api-client's response interceptor refreshes the access token
// transparently on a 401; it only calls this when that refresh itself
// failed, meaning the refresh token is gone too and the session is
// genuinely over. Subscribed once at module load -- not inside the hook --
// so it fires regardless of which component currently has a useAuth() call
// mounted.
onSessionExpired(() => {
  setState({ user: null, isLoading: false, isImpersonating: false });
});

// Shared by bootstrap() (below, cached as a singleton) and endImpersonation()
// (which needs a *fresh* refresh+/me cycle every time it's called, not the
// cached bootstrap promise) -- refreshes the access token from whichever
// httpOnly refresh cookie the browser is currently holding and resolves the
// resulting session.
async function establishSession(): Promise<void> {
  try {
    const { data: refreshed } = await refreshApiV1AuthRefreshPost({
      throwOnError: true,
    });
    const accessToken = refreshed.data?.access_token ?? null;
    setAccessToken(accessToken);
    const { data: me } = await meApiV1AuthMeGet({ throwOnError: true });
    setState({
      user: me.data ?? null,
      isLoading: false,
      isImpersonating: getImpersonatorId(accessToken) !== null,
    });
  } catch {
    setAccessToken(null);
    setState({ user: null, isLoading: false, isImpersonating: false });
  }
}

// A page reload loses the in-memory access token but not the httpOnly
// refresh cookie, so on first mount we try to silently re-establish the
// session before any route guard decides the user is logged out. Cached as
// a singleton promise so concurrent useAuth() callers (AppRoot + any page
// mounted underneath it) trigger exactly one bootstrap attempt.
let bootstrapPromise: Promise<void> | null = null;

function bootstrap(): Promise<void> {
  bootstrapPromise ??= establishSession();
  return bootstrapPromise;
}

export interface UseAuthResult {
  user: UserPublic | null;
  isLoading: boolean;
  isImpersonating: boolean;
  login: (email: string, password: string) => Promise<UserPublic>;
  logout: () => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  endImpersonation: () => Promise<void>;
  loadImpersonatedSession: (accessToken: string, user: UserPublic) => void;
  completeTwoFactorLogin: (accessToken: string, user: UserPublic) => void;
}

export function useAuth(): UseAuthResult {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);

  useEffect(() => {
    void bootstrap();
  }, []);

  const login = useCallback(
    async (email: string, password: string): Promise<UserPublic> => {
      const { data: envelope } = await loginApiV1AuthLoginPost({
        body: { email, password },
        throwOnError: true,
      });
      const payload = envelope.data;
      // `pending_2fa` is declared optional in the generated type (it mirrors
      // the backend's `= True` default), so narrowing on it would not
      // actually exclude TwoFactorRequiredResponse below -- an optional key
      // can be absent at runtime even on the type that declares it. Narrow on
      // `temp_token` instead, which is required and exclusive to that branch.
      if (payload && "temp_token" in payload) {
        throw new TwoFactorRequiredError(payload.temp_token);
      }
      if (!payload) {
        throw new Error("Login response was empty.");
      }
      applySession(payload.access_token, payload.user);
      return payload.user;
    },
    [],
  );

  const register = useCallback(
    async (email: string, password: string): Promise<void> => {
      await registerApiV1AuthRegisterPost({
        body: { email, password },
        throwOnError: true,
      });
    },
    [],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await logoutApiV1AuthLogoutPost({ throwOnError: true });
    } finally {
      setAccessToken(null);
      setState({ user: null, isImpersonating: false });
    }
  }, []);

  // T-142: ends the impersonation session server-side (revokes the jti),
  // then falls back to the superadmin's own refresh cookie -- impersonation
  // never touches or replaces it (see backend's AdminImpersonationService
  // docstring), so this silently restores the real admin session instead of
  // forcing a fresh login.
  const endImpersonation = useCallback(async (): Promise<void> => {
    const token = getAccessToken();
    try {
      await fetch(`${getApiBaseUrl()}${IMPERSONATE_END_PATH}`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        credentials: "include",
      });
    } finally {
      setAccessToken(null);
    }
    await establishSession();
  }, []);

  // T-143: the admin UI's impersonate-user modal calls
  // POST /admin/users/{id}/impersonate directly (that endpoint, like the
  // rest of /admin/*, has no generated SDK function -- see @forge/api-client's
  // admin.ts), then hands the resulting access_token/user pair here to load
  // it into the same session state login() would have produced, without
  // repeating login()'s own state-setting logic at the call site.
  const loadImpersonatedSession = useCallback(
    (accessToken: string, impersonatedUser: UserPublic): void => {
      applySession(accessToken, impersonatedUser);
    },
    [],
  );

  // T-217: the TwoFactorChallenge step (LoginPage) calls this once it's
  // traded the temp_token from a TwoFactorRequiredError for real tokens --
  // via either POST /auth/2fa/login (TOTP code/backup code) or the WebAuthn
  // login/options+login pair -- completing the login that `login()` above
  // deferred.
  const completeTwoFactorLogin = useCallback(
    (accessToken: string, user: UserPublic): void => {
      applySession(accessToken, user);
    },
    [],
  );

  return {
    user: snapshot.user,
    isLoading: snapshot.isLoading,
    isImpersonating: snapshot.isImpersonating,
    login,
    logout,
    register,
    endImpersonation,
    loadImpersonatedSession,
    completeTwoFactorLogin,
  };
}
