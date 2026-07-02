import { useEffect, useState } from "react";

import { getSubscriptionApiV1BillingSubscriptionGet, listPlansApiV1BillingPlansGet } from "@forge/api-client";

import { useOrg } from "./useOrg";

// Unlike useOrg/usePermission, this isn't a module-scoped singleton: a flag
// check is cheap, per-component, read-only state, not identity the rest of
// the app shell needs to agree on. Resolves to false (rather than throwing)
// for a member who lacks BILLING_READ -- a feature gate degrading to "off"
// is the right failure mode for someone who can't see billing at all.
export function useFeatureFlag(flag: string): boolean {
  const { org } = useOrg();
  const orgId = org?.id ?? null;
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    if (!orgId) {
      setEnabled(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const [subResult, plansResult] = await Promise.all([
          getSubscriptionApiV1BillingSubscriptionGet({ throwOnError: true }),
          listPlansApiV1BillingPlansGet({ throwOnError: true }),
        ]);
        if (cancelled) return;
        const subscription = subResult.data.data;
        if (!subscription) {
          setEnabled(false);
          return;
        }
        const plans = plansResult.data.data ?? [];
        const plan = plans.find((candidate) => candidate.id === subscription.plan_id);
        setEnabled(Boolean(plan?.features[flag]));
      } catch {
        if (!cancelled) {
          setEnabled(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgId, flag]);

  return enabled;
}
