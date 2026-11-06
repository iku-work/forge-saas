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

// The `sign_in_error` values the backend's callbacks can send that deserve their
// own wording. Anything else, including a value someone typed into a link, gets
// the general message. A Map because `?sign_in_error=constructor` must not find
// something on Object.prototype.
const SIGN_IN_ERROR_KEYS = new Map([
  ["cancelled", "auth.oauth_callback.cancelled"],
  ["errors.oauth_email_not_verified", "errors.oauth_email_not_verified"],
  ["errors.oauth_no_email", "errors.oauth_no_email"],
  ["errors.seat_limit_reached_to_join", "errors.seat_limit_reached_to_join"],
  ["errors.signup_disabled", "auth.register.signup_closed"],
  // Not from a callback: AppRoot sends it when an action needed a newer sign-in.
  ["errors.step_up_auth_required", "errors.step_up_auth_required"],
]);

export function signInErrorKey(reason: string): string {
  return SIGN_IN_ERROR_KEYS.get(reason) ?? "auth.oauth_callback.error_message";
}

// An account with a second factor comes back from its identity provider with a
// temp token in the URL fragment (browser_sign_in_redirect in the backend's
// auth/router.py). Reading and removing are separate so that reading stays safe
// inside a useState initializer, which React may run twice.
export function readTempTokenFromHash(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("temp_token");
}

export function removeTempTokenFromHash(): void {
  if (readTempTokenFromHash() !== null) {
    const { pathname, search } = window.location;
    window.history.replaceState(null, "", `${pathname}${search}`);
  }
}

// A plain document redirect, not an XHR/fetch call -- the provider's
// consent screen and the callback's Set-Cookie response both require a
// full top-level navigation.
export function getOAuthStartUrl(provider: string): string {
  return `${getApiBaseUrl()}/api/v1/auth/oauth/${provider}`;
}
