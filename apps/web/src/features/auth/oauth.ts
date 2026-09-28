import { getApiBaseUrl } from "@forge/api-client";

// UX-01: the providers this build can render a button for, mapped to their
// @forge/i18n label keys. `/config` says which are *configured*; this says
// which we have copy for. The login page renders the intersection, so a
// provider added to the backend before its string lands shows nothing
// rather than an unlabelled button.
export const OAUTH_PROVIDER_LABEL_KEYS: Record<string, string> = {
  google: "auth.login.continue_google",
  github: "auth.login.continue_github",
};

// A plain document redirect, not an XHR/fetch call -- the provider's
// consent screen and the callback's Set-Cookie response both require a
// full top-level navigation.
export function getOAuthStartUrl(provider: string): string {
  return `${getApiBaseUrl()}/api/v1/auth/oauth/${provider}`;
}
