import type { ResolvedRequestOptions } from "./generated/client/types.gen";
import { getApiBaseUrl, setAccessToken } from "./client";

const REFRESH_PATH = "/api/v1/auth/refresh";
// Marks a request as already having gone through one refresh-and-retry
// cycle, so a 401 on the retry itself is reported as-is instead of looping.
const RETRY_HEADER = "X-Auth-Retried";

// Endpoints whose 401 reflects a bad/expired/wrong *credential supplied in
// that request* (a password, a verification/reset/magic link, a 2FA/WebAuthn
// challenge, an email-change confirmation) -- never the caller's own login
// session. Each is reachable while logged out, logged in as someone else, or
// logged in as the account the link belongs to, so treating a 401 here as
// "session expired" is wrong regardless of whether an access token happens
// to be sitting in memory (the client attaches one to every request when
// present -- see client.ts's `auth` callback -- whether or not the endpoint
// needs it). Left uncaught, `notifySessionExpired()` force-navigates to
// /login (see apps/web/src/AppRoot.tsx) before the caller's own error
// handling -- e.g. VerifyEmailPage's or ConfirmEmailChangePage's "this link
// is invalid or expired" state -- ever gets a chance to render.
const SESSION_INDEPENDENT_PATHS = [
  "/api/v1/auth/login",
  "/api/v1/auth/verify-email",
  "/api/v1/auth/reset-password",
  "/api/v1/auth/magic-link/consume",
  "/api/v1/auth/change-email/confirm",
  "/api/v1/auth/2fa/login",
  "/api/v1/auth/webauthn/login",
  "/api/v1/auth/webauthn/login/options",
];

function isSessionIndependentRequest(url: string): boolean {
  return SESSION_INDEPENDENT_PATHS.some((path) => url.includes(path));
}

interface RefreshEnvelope {
  data?: { access_token?: string } | null;
}

async function performRefresh(): Promise<string | null> {
  try {
    const response = await fetch(`${getApiBaseUrl()}${REFRESH_PATH}`, {
      method: "POST",
      credentials: "include",
    });
    if (!response.ok) {
      return null;
    }
    const envelope = (await response.json()) as RefreshEnvelope;
    return envelope.data?.access_token ?? null;
  } catch {
    return null;
  }
}

// Concurrent 401s (e.g. several authenticated requests firing around the
// same moment the access token expires) must share one refresh call rather
// than each racing the refresh endpoint -- the refresh token rotates on use
// (T-019), so a second concurrent call would revoke the session the first
// call just established.
let refreshPromise: Promise<string | null> | null = null;

function refreshAccessTokenOnce(): Promise<string | null> {
  refreshPromise ??= performRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

const sessionExpiredListeners = new Set<() => void>();

// Lets @forge/hooks's useAuth (and the web app's router) react when a
// refresh-and-retry cycle determines the session is genuinely over, without
// this package depending on either of them.
export function onSessionExpired(listener: () => void): () => void {
  sessionExpiredListeners.add(listener);
  return () => {
    sessionExpiredListeners.delete(listener);
  };
}

function notifySessionExpired(): void {
  for (const listener of sessionExpiredListeners) {
    listener();
  }
}

// The access token is short-lived (15 min) and kept only in memory, so a 401
// is the normal outcome of an ordinary session, not an error path: refresh
// it from the httpOnly cookie and transparently replay the original request.
// Registered as a response interceptor (see index.ts) so every generated SDK
// call gets this for free -- callers never see the intermediate 401.
export async function authRetryInterceptor(
  response: Response,
  request: Request,
  options: ResolvedRequestOptions,
): Promise<Response> {
  if (response.status !== 401) {
    return response;
  }
  if (
    request.url.includes(REFRESH_PATH) ||
    request.headers.has(RETRY_HEADER) ||
    isSessionIndependentRequest(request.url)
  ) {
    return response;
  }

  const newToken = await refreshAccessTokenOnce();
  if (!newToken) {
    setAccessToken(null);
    notifySessionExpired();
    return response;
  }

  setAccessToken(newToken);

  const retryHeaders = new Headers(request.headers);
  retryHeaders.set("Authorization", `Bearer ${newToken}`);
  retryHeaders.set(RETRY_HEADER, "1");

  return fetch(request.url, {
    method: request.method,
    headers: retryHeaders,
    body: options.serializedBody,
    credentials: request.credentials,
  });
}
