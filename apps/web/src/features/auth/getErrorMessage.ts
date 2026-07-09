import i18next from "i18next";

// Every error response from the API -- including FastAPI's own 422s, which
// the backend's validation_error_handler re-wraps -- comes back as
// `Envelope[None]` (`{ data: null, error: { code, message_key, message,
// details }, meta }`). The generated client types don't capture this for
// non-422 status codes (hey-api only types responses FastAPI documents in
// the OpenAPI schema), so this reads the shape defensively instead of
// trusting a generated type.
interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message_key?: string;
    message?: string;
  } | null;
}

function hasErrorCode(value: unknown): value is ApiErrorEnvelope {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ApiErrorEnvelope).error?.code === "string"
  );
}

// `fallbackKey` is a @forge/i18n translation key, not English text -- every
// call site passes one of its own namespace's `error_default`-style keys
// (see e.g. auth.json's "login.error_default"). The backend's
// error.message is never shown directly: it's already localised
// server-side from request.state.locale (T-068), which can disagree with
// whatever locale the user has picked in this tab, so the code is
// re-translated through the frontend's own catalog instead.
//
// T-218: `message_key` (e.g. "errors.email_unchanged") is tried before the
// coarser `code` (e.g. "conflict") -- many distinct DomainError subclasses
// share one `code` (every ConflictError is "conflict", every
// AuthenticationError is "authentication_failed", ...), which collapsed
// unrelated failures (an email that's already yours vs. an already-enabled
// 2FA setup vs. any other conflict) down to the same generic "This action
// conflicts with the resource's current state." `message_key` already
// names the exact catalog entry the backend raised with -- most of which
// (e.g. "errors.email_unchanged", "errors.totp_already_enabled") already
// exist in packages/i18n/locales precisely because the backend uses them
// too, they just weren't reachable from here before this field existed.
export function getErrorMessage(error: unknown, fallbackKey: string): string {
  if (hasErrorCode(error)) {
    const { code, message_key: messageKey } = error.error ?? {};
    if (messageKey && i18next.exists(messageKey)) {
      return i18next.t(messageKey);
    }
    const codeKey = `errors.${code ?? ""}`;
    if (code && i18next.exists(codeKey)) {
      return i18next.t(codeKey);
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return i18next.t(fallbackKey);
}
