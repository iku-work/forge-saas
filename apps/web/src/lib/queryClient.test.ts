/**
 * The retry policy, pinned to the failure that produced it.
 *
 * A fresh org has no Subscription row and therefore no entitlements, so every
 * storage route answers 402. With TanStack Query's default `retry: 3` the
 * dashboard issued four requests per mount and repeated them on every window
 * focus -- a request whose answer was known before it was sent, sent four
 * times. Retrying a 4xx cannot change the outcome; only the caller can.
 */
import { HTTP_STATUS } from "@forge/api-client";
import { describe, expect, it } from "vitest";

import { shouldRetry } from "./queryClient";

/** The shape the generated client throws: the envelope, annotated by the
 * error interceptor with the status the Response carried. */
function apiError(status: number): unknown {
  const error = { data: null, error: { code: "plan_limit", message: "nope" }, meta: {} };
  Object.defineProperty(error, HTTP_STATUS, { value: status, enumerable: false });
  return error;
}

describe("shouldRetry", () => {
  it("does not retry the 402 that caused this", () => {
    expect(shouldRetry(0, apiError(402))).toBe(false);
  });

  it.each([400, 401, 403, 404, 409, 422])("does not retry %i", (status) => {
    expect(shouldRetry(0, apiError(status))).toBe(false);
  });

  it.each([408, 429])("still retries %i, which is a genuine 'try again'", (status) => {
    expect(shouldRetry(0, apiError(status))).toBe(true);
  });

  it("retries server errors", () => {
    expect(shouldRetry(0, apiError(503))).toBe(true);
  });

  it("retries an error with no status — it never reached the server", () => {
    expect(shouldRetry(0, new TypeError("Failed to fetch"))).toBe(true);
  });

  it("still gives up eventually, so a flapping endpoint cannot loop forever", () => {
    expect(shouldRetry(2, apiError(503))).toBe(true);
    expect(shouldRetry(3, apiError(503))).toBe(false);
    expect(shouldRetry(3, apiError(429))).toBe(false);
  });

  it("tolerates a thrown value that is not an object", () => {
    expect(shouldRetry(0, "boom")).toBe(true);
    expect(shouldRetry(0, undefined)).toBe(true);
  });
});
