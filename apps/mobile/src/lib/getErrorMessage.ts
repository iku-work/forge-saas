// Mirrors apps/web/src/features/auth/getErrorMessage.ts. Every error
// response from the API -- including FastAPI's own 422s -- comes back as
// `Envelope[None]` (`{ data: null, error: { code, message, details }, meta }`).
// The generated client types don't capture this for non-422 status codes
// (hey-api only types responses FastAPI documents in the OpenAPI schema),
// so this reads the shape defensively instead of trusting a generated type.
import i18next from "i18next";

interface ApiErrorEnvelope {
  error?: {
    code?: string;
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
// call site passes one of its own namespace's `error_default`-style keys.
// The backend's error.message is never shown directly: it's already
// localised server-side from request.state.locale (T-068), which can
// disagree with whatever locale this device has picked, so the code is
// re-translated through the app's own catalog instead (the "errors.<code>"
// mapping T-069 describes, applied here for T-070).
export function getErrorMessage(error: unknown, fallbackKey: string): string {
  if (hasErrorCode(error)) {
    const code = error.error?.code;
    const key = `errors.${code ?? ""}`;
    if (code && i18next.exists(key)) {
      return i18next.t(key);
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return i18next.t(fallbackKey);
}
