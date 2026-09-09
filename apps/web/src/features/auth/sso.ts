import { getApiBaseUrl } from "@forge/api-client";

// A plain document redirect, not a fetch -- same reason as `getOAuthStartUrl`:
// the IdP's own sign-in page and the callback's `Set-Cookie` both need a full
// top-level navigation. Encoded because the slug reaches us from an API
// response rather than from a route param.
export function getSsoStartUrl(orgSlug: string): string {
  return `${getApiBaseUrl()}/api/v1/auth/sso/${encodeURIComponent(orgSlug)}/start`;
}
