import { useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";

import {
  listFeatureFlagsApiV1OrgsOrgIdFeatureFlagsGet,
  listFeatureFlagsApiV1OrgsOrgIdFeatureFlagsGetQueryKey,
} from "@forge/api-client";
import type { EnvelopeFeatureFlagsResponse } from "@forge/api-client";

import { useOrg } from "./useOrg";

const STALE_TIME_MS = 60_000;

// Resolved once per (org, 60s) via TanStack Query -- org.id in the query key
// (see MembersSettingsPage.tsx for the same `org?.id ?? ""` + `enabled`
// pattern) means switching orgs is a normal cache-miss refetch, not a
// manual invalidation this hook has to orchestrate itself.
export function useFeatureFlags(): UseQueryResult<EnvelopeFeatureFlagsResponse> {
  const { org } = useOrg();

  return useQuery({
    queryKey: listFeatureFlagsApiV1OrgsOrgIdFeatureFlagsGetQueryKey({
      path: { org_id: org?.id ?? "" },
    }),
    queryFn: async () => {
      const { data } = await listFeatureFlagsApiV1OrgsOrgIdFeatureFlagsGet({
        path: { org_id: org?.id ?? "" },
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org),
    staleTime: STALE_TIME_MS,
  });
}

// Deny-by-default: no data yet (loading, no org, or a failed fetch) reads as
// disabled rather than throwing -- a feature gate degrading to "off" is the
// right failure mode for a flag that can't be resolved yet.
export function useFeatureFlag(flag: string): boolean {
  const { data } = useFeatureFlags();
  return Boolean(data?.data?.flags[flag]);
}
