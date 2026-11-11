import { useQuery } from "@tanstack/react-query";

import { getOrgLogoApiV1OrgsOrgIdLogoGet } from "@forge/api-client";
import type { OrganizationPublic } from "@forge/api-client";

// Review R7: a logo can be chosen before its malware scan finishes, and
// `/orgs/{id}/logo` answers 409 `file_scan_pending` until it has. That is
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

export function scanRetry(failureCount: number, error: unknown): boolean {
  return isScanPending(error)
    ? failureCount < SCAN_PENDING_RETRIES
    : failureCount < 3;
}

export function scanRetryDelay(attempt: number, error: unknown): number {
  return isScanPending(error)
    ? SCAN_PENDING_RETRY_DELAY_MS
    : Math.min(1_000 * 2 ** attempt, 30_000);
}

/** One key per organization, so the switcher and the settings page share it. */
export function orgLogoQueryKey(orgId: string): readonly unknown[] {
  return ["orgs", orgId, "logo"] as const;
}

/**
 * An organization's logo image URL, or null when there is nothing to show.
 *
 * The logos bucket is private, so what the page gets is a presigned link that lives five minutes, fetched only for an
 * org that has an uploaded logo, retried while its scan is still pending, and
 * refetched before it expires. `logo_url`, an external URL set some other
 * way, is the fallback.
 */
export function useOrgLogoSrc(org: OrganizationPublic | null): string | null {
  const orgId = org?.id ?? "";
  const logoQuery = useQuery({
    queryKey: orgLogoQueryKey(orgId),
    enabled: org?.has_uploaded_logo === true,
    staleTime: 4 * 60 * 1000,
    retry: scanRetry,
    retryDelay: scanRetryDelay,
    queryFn: async () => {
      const { data } = await getOrgLogoApiV1OrgsOrgIdLogoGet({
        path: { org_id: orgId },
        throwOnError: true,
      });
      return data;
    },
  });
  return logoQuery.data?.data?.url ?? org?.logo_url ?? null;
}
