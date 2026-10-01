import { useQuery } from "@tanstack/react-query";

import { getMyAvatarApiV1AuthMeAvatarGet } from "@forge/api-client";
import { useAuth } from "@forge/hooks";

/** Shared so a writer can invalidate the exact key the readers subscribe to. */
export const AVATAR_QUERY_KEY = ["me", "avatar"] as const;

// Review R7: an avatar can be chosen before its malware scan finishes, and
// `/auth/me/avatar` answers 409 `file_scan_pending` until it has. That is
// "not yet", not "broken" -- keep asking for a while (about two minutes),
// showing the fallback meanwhile, instead of giving up after one try.
const SCAN_PENDING_RETRIES = 24;
const SCAN_PENDING_RETRY_DELAY_MS = 5_000;

export function isScanPending(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "error" in error &&
    (error as { error?: { message_key?: string } }).error?.message_key ===
      "errors.file_scan_pending"
  );
}

/**
 * The current user's avatar image URL, or null when there is nothing to show.
 *
 * This used to live inline in ProfileSettingsPage, which meant that page was
 * the only surface in the app that ever rendered the picture: the shell's
 * user menu -- the avatar you actually look at, in the corner, on every
 * screen -- had an `<AvatarFallback>` and no `<AvatarImage>`, so it showed
 * initials forever. Sharing the query rather than the markup is what keeps
 * the two from drifting again, and TanStack Query dedupes the request across
 * however many components mount it.
 *
 * The avatars bucket is private, so there is no URL on the user object: what
 * `/auth/me/avatar` returns is a presigned link that expires in five
 * minutes. It is only requested when the user actually has an uploaded
 * avatar -- an OAuth `picture` is already an absolute URL on
 * `user.avatar_url`, and costs no request at all.
 */
export function avatarRetry(failureCount: number, error: unknown): boolean {
  return isScanPending(error)
    ? failureCount < SCAN_PENDING_RETRIES
    : failureCount < 3;
}

export function avatarRetryDelay(attempt: number, error: unknown): number {
  return isScanPending(error)
    ? SCAN_PENDING_RETRY_DELAY_MS
    : Math.min(1_000 * 2 ** attempt, 30_000);
}

export function useAvatarSrc(): string | null {
  const { user } = useAuth();

  const avatarQuery = useQuery({
    queryKey: AVATAR_QUERY_KEY,
    enabled: user?.has_uploaded_avatar === true,
    // Refetched well inside the signature's lifetime: a stale src would
    // simply stop loading, with nothing on the page to explain why.
    staleTime: 4 * 60 * 1000,
    retry: avatarRetry,
    retryDelay: avatarRetryDelay,
    queryFn: async () => {
      const { data } = await getMyAvatarApiV1AuthMeAvatarGet({
        throwOnError: true,
      });
      return data;
    },
  });

  // An uploaded avatar wins over an OAuth picture: the upload is a deliberate
  // choice, the picture is a default someone else supplied.
  return avatarQuery.data?.data?.url ?? user?.avatar_url ?? null;
}
