/**
 * Mobile's own Client instance -- deliberately not @forge/api-client's
 * default singleton (packages/api-client/src/index.ts), which is wired for
 * the browser: it resolves its base URL from Vite's `import.meta.env` and
 * authenticates via the httpOnly refresh cookie the browser sends
 * automatically. Neither exists in this runtime. Per forgesaas-plan.html's
 * "Auth token handling on mobile" note, there are no cookies on mobile --
 * the access and refresh tokens are managed explicitly, persisted in
 * expo-secure-store (Keychain/Keystore), and never AsyncStorage.
 *
 * apps/mobile/src/lib/auth.ts (the higher-level login/logout/getAccessToken
 * API) depends on this file for the configured `client` and the storage
 * helpers; this file does not depend on auth.ts, so the two never form an
 * import cycle.
 */
import {
  createClient,
  createConfig,
  orgIdRequestInterceptor,
  type Client,
} from "@forge/api-client";
import * as SecureStore from "expo-secure-store";

export const ACCESS_TOKEN_KEY = "forge_access_token";
export const REFRESH_TOKEN_KEY = "forge_refresh_token";

const REFRESH_PATH = "/api/v1/auth/refresh";
const PLATFORM_HEADER = "X-Client-Platform";
const MOBILE_PLATFORM = "mobile";
// Marks a request as already having gone through one refresh-and-retry
// cycle, so a 401 on the retry itself is reported as-is instead of looping --
// mirrors packages/api-client's authInterceptor.ts.
const RETRY_HEADER = "X-Auth-Retried";

function getApiBaseUrl(): string {
  return process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000";
}

// In-memory cache so the request interceptor's Authorization header doesn't
// pay a SecureStore round-trip on every call. Hydrated once at cold start
// (see auth.ts's bootstrapSession) and kept in sync by setStoredTokens /
// clearStoredTokens below.
let cachedAccessToken: string | null = null;

export function getCachedAccessToken(): string | null {
  return cachedAccessToken;
}

export async function hydrateAccessTokenCache(): Promise<string | null> {
  cachedAccessToken = await SecureStore.getItemAsync(ACCESS_TOKEN_KEY);
  return cachedAccessToken;
}

export async function getStoredRefreshToken(): Promise<string | null> {
  return SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
}

export async function setStoredTokens(
  accessToken: string,
  refreshToken: string,
): Promise<void> {
  cachedAccessToken = accessToken;
  await Promise.all([
    SecureStore.setItemAsync(ACCESS_TOKEN_KEY, accessToken),
    SecureStore.setItemAsync(REFRESH_TOKEN_KEY, refreshToken),
  ]);
}

export async function clearStoredTokens(): Promise<void> {
  cachedAccessToken = null;
  await Promise.all([
    SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY),
    SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY),
  ]);
}

const sessionExpiredListeners = new Set<() => void>();

// Lets auth.ts's bootstrap/_layout.tsx react when a refresh-and-retry cycle
// determines the session is genuinely over (refresh token gone too), without
// this module depending on expo-router for navigation -- mirrors
// packages/api-client's authInterceptor.ts onSessionExpired.
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

interface RefreshEnvelope {
  data?: { access_token?: string; refresh_token?: string | null } | null;
}

async function performRefresh(): Promise<string | null> {
  const refreshToken = await getStoredRefreshToken();
  if (!refreshToken) {
    return null;
  }
  try {
    const response = await fetch(`${getApiBaseUrl()}${REFRESH_PATH}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [PLATFORM_HEADER]: MOBILE_PLATFORM,
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok) {
      return null;
    }
    const envelope = (await response.json()) as RefreshEnvelope;
    const newAccessToken = envelope.data?.access_token;
    const newRefreshToken = envelope.data?.refresh_token;
    if (!newAccessToken || !newRefreshToken) {
      return null;
    }
    await setStoredTokens(newAccessToken, newRefreshToken);
    return newAccessToken;
  } catch {
    return null;
  }
}

// Concurrent 401s (several authenticated requests firing around the same
// moment the access token expires) must share one refresh call rather than
// each racing the refresh endpoint -- the refresh token rotates on use, so a
// second concurrent call would revoke the session the first call just
// established. Mirrors packages/api-client's authInterceptor.ts.
let refreshPromise: Promise<string | null> | null = null;

function refreshAccessTokenOnce(): Promise<string | null> {
  refreshPromise ??= performRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

export const client: Client = createClient(
  createConfig({
    baseUrl: getApiBaseUrl(),
    headers: { [PLATFORM_HEADER]: MOBILE_PLATFORM },
  }),
);

client.interceptors.request.use((request) => {
  if (!cachedAccessToken) {
    return request;
  }
  const headers = new Headers(request.headers);
  headers.set("Authorization", `Bearer ${cachedAccessToken}`);
  return new Request(request, { headers });
});

// @forge/api-client only wires this onto its own default (browser) singleton
// (packages/api-client/src/index.ts) -- registered here too so every
// org-scoped call this app's own `client` makes carries the header
// automatically, same as web. See ./org.ts for how mobile resolves which org
// to pin, since there's no org switcher here yet.
client.interceptors.request.use(orgIdRequestInterceptor);

client.interceptors.response.use(async (response, request, options) => {
  if (response.status !== 401) {
    return response;
  }
  if (request.url.includes(REFRESH_PATH) || request.headers.has(RETRY_HEADER)) {
    return response;
  }

  const newAccessToken = await refreshAccessTokenOnce();
  if (!newAccessToken) {
    await clearStoredTokens();
    notifySessionExpired();
    return response;
  }

  const retryHeaders = new Headers(request.headers);
  retryHeaders.set("Authorization", `Bearer ${newAccessToken}`);
  retryHeaders.set(RETRY_HEADER, "1");
  return fetch(request.url, {
    method: request.method,
    headers: retryHeaders,
    body: options.serializedBody,
  });
});
