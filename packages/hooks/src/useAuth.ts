import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  loginApiV1AuthLoginPost,
  logoutApiV1AuthLogoutPost,
  meApiV1AuthMeGet,
  onSessionExpired,
  refreshApiV1AuthRefreshPost,
  registerApiV1AuthRegisterPost,
  setAccessToken,
} from "@forge/api-client";
import type { UserPublic } from "@forge/api-client";

// Thrown when /auth/login responds with a 2FA challenge instead of tokens.
// Phase 1 has no 2FA-verification UI, so callers surface this as a regular
// form error rather than the hook silently completing a half-login.
export class TwoFactorRequiredError extends Error {
  constructor(public readonly tempToken: string) {
    super("Two-factor authentication is required to finish signing in.");
    this.name = "TwoFactorRequiredError";
  }
}

interface AuthState {
  user: UserPublic | null;
  isLoading: boolean;
}

// Module-scoped store (not React Context) so every `useAuth()` call site --
// AppRoot, the login form, the app shell's user menu -- reads and writes the
// same session without a provider component, and so the hook file itself
// never needs JSX.
let state: AuthState = { user: null, isLoading: true };
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

// The api-client's response interceptor refreshes the access token
// transparently on a 401; it only calls this when that refresh itself
// failed, meaning the refresh token is gone too and the session is
// genuinely over. Subscribed once at module load -- not inside the hook --
// so it fires regardless of which component currently has a useAuth() call
// mounted.
onSessionExpired(() => {
  setState({ user: null, isLoading: false });
});

// A page reload loses the in-memory access token but not the httpOnly
// refresh cookie, so on first mount we try to silently re-establish the
// session before any route guard decides the user is logged out. Cached as
// a singleton promise so concurrent useAuth() callers (AppRoot + any page
// mounted underneath it) trigger exactly one bootstrap attempt.
let bootstrapPromise: Promise<void> | null = null;

function bootstrap(): Promise<void> {
  bootstrapPromise ??= (async () => {
    try {
      const { data: refreshed } = await refreshApiV1AuthRefreshPost({
        throwOnError: true,
      });
      setAccessToken(refreshed.data?.access_token ?? null);
      const { data: me } = await meApiV1AuthMeGet({ throwOnError: true });
      setState({ user: me.data ?? null, isLoading: false });
    } catch {
      setAccessToken(null);
      setState({ user: null, isLoading: false });
    }
  })();
  return bootstrapPromise;
}

export interface UseAuthResult {
  user: UserPublic | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<UserPublic>;
  logout: () => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
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
      setAccessToken(payload.access_token);
      setState({ user: payload.user });
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
      setState({ user: null });
    }
  }, []);

  return {
    user: snapshot.user,
    isLoading: snapshot.isLoading,
    login,
    logout,
    register,
  };
}
