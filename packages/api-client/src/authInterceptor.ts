import type { ResolvedRequestOptions } from "./generated/client/types.gen";
import { getApiBaseUrl, setAccessToken } from "./client";

const REFRESH_PATH = "/api/v1/auth/refresh";
// Marks a request as already having gone through one refresh-and-retry
// cycle, so a 401 on the retry itself is reported as-is instead of looping.
const RETRY_HEADER = "X-Auth-Retried";

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
  if (request.url.includes(REFRESH_PATH) || request.headers.has(RETRY_HEADER)) {
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
