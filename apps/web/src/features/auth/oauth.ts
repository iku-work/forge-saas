import { getApiBaseUrl } from "@forge/api-client";

export type OAuthProvider = "google" | "github";

// A plain document redirect, not an XHR/fetch call -- the provider's
// consent screen and the callback's Set-Cookie response both require a
// full top-level navigation.
export function getOAuthStartUrl(provider: OAuthProvider): string {
  return `${getApiBaseUrl()}/api/v1/auth/oauth/${provider}`;
}
