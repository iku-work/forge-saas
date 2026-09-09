import { httpStatusOf } from "@forge/api-client";
import { QueryClient } from "@tanstack/react-query";

/**
 * The shared QueryClient, and specifically its retry policy.
 *
 * TanStack Query defaults to `retry: 3` with backoff, which is a good default
 * for the failure it is designed around -- a request that might succeed if
 * tried again. It is the wrong default for a 4xx, which is the server saying
 * the request itself is unacceptable. Retrying that cannot change the answer.
 *
 * This was not theoretical. The storage routes used to sit behind
 * `require_feature("storage_gb")` and answered 402 for any org without a
 * subscription, while the dashboard requested `/api/v1/files` four times per
 * mount and again on every window focus -- filling the console and the access
 * log with requests whose outcome was known in advance. That gate is gone
 * (the quota replaced it, so a free org is no longer refused for existing),
 * but 402 has not: exceed the plan's storage_gb, max_upload_mb or max_files
 * and the answer is still Payment Required, and still not worth four tries.
 *
 * Two statuses are deliberately still retried. 408 is a timeout, which is a
 * "try again" by definition, and 429 means the server asked for a delay
 * rather than refusing the request -- backing off is exactly the right
 * response.
 *
 * 401 never reaches here: `authRetryInterceptor` refreshes the token and
 * replays the request inside the client, so a 401 that survives to this point
 * is a genuinely dead session and must not be retried either.
 */
const RETRYABLE_CLIENT_ERRORS = new Set([408, 429]);
const MAX_RETRIES = 3;

export function shouldRetry(failureCount: number, error: unknown): boolean {
  const status = httpStatusOf(error);
  if (status !== undefined && status >= 400 && status < 500) {
    return RETRYABLE_CLIENT_ERRORS.has(status) && failureCount < MAX_RETRIES;
  }
  // No status means it never reached the server -- a network error, which is
  // the case retries exist for. 5xx likewise.
  return failureCount < MAX_RETRIES;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry },
      // A mutation that failed on a 4xx is even less worth repeating: it may
      // have side effects, and the user is waiting on the answer.
      mutations: { retry: shouldRetry },
    },
  });
}
