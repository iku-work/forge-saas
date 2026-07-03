import type { CreateClientConfig } from "./generated/client.gen";

// Vite injects `import.meta.env` at build time; this package has no direct
// dependency on `vite`, so the shape is declared locally instead of pulling
// in `vite/client` types.
declare global {
  interface ImportMetaEnv {
    readonly VITE_API_URL?: string;
  }
  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }
}

export function getApiBaseUrl(): string {
  return import.meta.env.VITE_API_URL ?? "http://localhost:8000";
}

// In-memory only -- the access token must never touch localStorage/cookies
// (§0.6); the refresh token is the httpOnly cookie the browser already
// manages. Module-scoped so the SDK's per-request `auth` callback and
// `@forge/hooks`'s useAuth share the same value without a Context provider.
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

// The organization the user has selected in the app shell's org switcher.
// Module-scoped for the same reason as accessToken: @forge/hooks's useOrg
// and the orgIdRequestInterceptor below need to share one value without a
// Context provider.
let currentOrgId: string | null = null;

export function getCurrentOrgId(): string | null {
  return currentOrgId;
}

export function setCurrentOrgId(orgId: string | null): void {
  currentOrgId = orgId;
}

// T-142: reads the `impersonator_id` claim off an access token's JWT payload
// -- client-side only, for the app shell's "you are impersonating" banner
// (UX, not an authorization decision; the backend is the sole authority on
// whether a token is actually valid). Returns null for a normal token, a
// malformed token, or no token at all.
function base64UrlDecode(segment: string): string {
  const padded = segment.replace(/-/g, "+").replace(/_/g, "/");
  const withPadding = padded + "=".repeat((4 - (padded.length % 4)) % 4);
  return atob(withPadding);
}

export function getImpersonatorId(token: string | null): string | null {
  if (!token) {
    return null;
  }
  const parts = token.split(".");
  const payloadSegment = parts[1];
  if (parts.length !== 3 || !payloadSegment) {
    return null;
  }
  try {
    const claims = JSON.parse(base64UrlDecode(payloadSegment)) as {
      impersonator_id?: string;
    };
    return claims.impersonator_id ?? null;
  } catch {
    return null;
  }
}

export const createClientConfig: CreateClientConfig = (config) => ({
  ...config,
  baseUrl: getApiBaseUrl(),
  credentials: "include",
  auth: () => accessToken ?? undefined,
});
