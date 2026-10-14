import { useQuery } from "@tanstack/react-query";

import { getOrgLogoApiV1OrgsOrgIdLogoGet } from "@forge/api-client";
import type { OrganizationPublic } from "@forge/api-client";

import { avatarRetry, avatarRetryDelay } from "./useAvatarSrc";

/** One key per organization, so the switcher and the settings page share it. */
export function orgLogoQueryKey(orgId: string): readonly unknown[] {
  return ["orgs", orgId, "logo"] as const;
}

/**
 * An organization's logo image URL, or null when there is nothing to show.
 *
 * The same shape as useAvatarSrc: the logos bucket is private, so what the
 * page gets is a presigned link that lives five minutes, fetched only for an
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
    retry: avatarRetry,
    retryDelay: avatarRetryDelay,
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
