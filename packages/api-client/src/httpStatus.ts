/**
 * The HTTP status, carried on the thrown error so callers can act on it.
 *
 * The generated client throws the *parsed error envelope* -- `{data, error,
 * meta}` -- and nothing else. That is the right thing for rendering a message
 * and useless for deciding what to do next, because the status is gone by the
 * time anybody catches it. A caller that wants "did this fail because the
 * plan does not allow it, or because the network blinked?" has to guess from
 * an error code string.
 *
 * That gap had a visible cost: TanStack Query's default `retry: 3` cannot
 * tell a 402 from a timeout, so every plan-gated route was requested four
 * times per mount and again on every window focus. See apps/web's QueryClient.
 *
 * An error interceptor is the only place both the Response and the thrown
 * value exist at once, so this is where the two get reunited. Attached as a
 * non-enumerable property: the envelope is handed to render code and to
 * `JSON.stringify` in tests, and a status appearing in those payloads would
 * be a surprise.
 */
export const HTTP_STATUS = Symbol.for("forge.httpStatus");

export interface WithHttpStatus {
  [HTTP_STATUS]?: number;
}

/** Reads the status off a thrown error, when one was attached. */
export function httpStatusOf(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  return (error as WithHttpStatus)[HTTP_STATUS];
}

/**
 * Registered as an error interceptor (see index.ts). Returns the error
 * unchanged apart from the annotation -- it must never swallow or replace
 * one, because every caller downstream still expects the envelope.
 *
 * `response` is genuinely optional: a network error produces no Response at
 * all, which is precisely the case retries exist for. Leaving no status on
 * those is what makes `httpStatusOf` return undefined and the retry policy
 * treat them as retryable.
 */
export function attachHttpStatus(error: unknown, response: Response | undefined): unknown {
  if (response !== undefined && typeof error === "object" && error !== null) {
    Object.defineProperty(error, HTTP_STATUS, {
      value: response.status,
      enumerable: false,
      configurable: true,
      writable: true,
    });
  }
  return error;
}
