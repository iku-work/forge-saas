import { useQuery } from "@tanstack/react-query";

import {
  getSupportContextApiV1MeSupportContextGet,
  getSupportContextApiV1MeSupportContextGetQueryKey,
} from "@forge/api-client";

import { useAuth } from "./useAuth";
import { useOrg } from "./useOrg";

// T-262: the fields a support conversation should start with, so the
// product's support widget can identify the user without the template ever
// importing a vendor SDK. Deliberately camelCase (and flattened) rather than
// the API's snake_case shape -- this is the contract product-side code reads,
// and it should not have to change if the endpoint's field names ever do.
export interface SupportContext {
  userId: string;
  email: string;
  fullName: string | null;
  orgId: string;
  orgName: string;
  // null when the org has no subscription row: a free-tier org, or a
  // billing-disabled product (see the backend's SupportContextResponse).
  plan: string | null;
  subscriptionStatus: string | null;
  signupAt: string;
  locale: string;
  // T-142: true when a superadmin is acting as this user. The product-side
  // wrapper MUST NOT call its support tool's identify/boot API in that case
  // -- see docs/runbooks/support-tool-integration.md. The operator's
  // clicking around would otherwise land in the real user's conversation
  // history, and the vendor's reply would go to someone who never asked.
  isImpersonating: boolean;
}

// One request per (user, org) rather than composing this client-side from
// useAuth + useOrg + a billing call: the subscription half isn't otherwise
// loaded on most pages, and mobile (which can't run any of these hooks --
// see apps/mobile/src/lib/support.ts) has to read the same shape from the
// same endpoint anyway. 5 minutes, because nothing here changes mid-session
// except impersonation, which swaps the access token and the user id with it.
const STALE_TIME_MS = 300_000;

/**
 * Returns null until there is a real, resolved context to hand a support
 * tool -- while auth is still bootstrapping, when nobody is signed in, and
 * while the request is in flight. A widget booted with a half-populated
 * identity is worse than one booted a moment later.
 */
export function useSupportContext(): SupportContext | null {
  const { user, isLoading } = useAuth();
  const { org } = useOrg();

  const { data } = useQuery({
    // The endpoint takes no path params, so the generated key is a constant
    // -- the org rides in on the X-Org-ID header (orgInterceptor.ts) and the
    // user on the bearer token, neither of which the key would otherwise
    // see. Both are appended so switching org, or starting/ending an
    // impersonation session, is a normal cache miss rather than a stale read.
    queryKey: [
      ...getSupportContextApiV1MeSupportContextGetQueryKey(),
      user?.id ?? "",
      org?.id ?? "",
    ],
    queryFn: async () => {
      const { data: envelope } =
        await getSupportContextApiV1MeSupportContextGet({
          throwOnError: true,
        });
      return envelope.data ?? null;
    },
    enabled: Boolean(user) && Boolean(org),
    staleTime: STALE_TIME_MS,
  });

  if (isLoading || !user || !org || !data) {
    return null;
  }

  return {
    userId: data.user_id,
    email: data.email,
    fullName: data.full_name,
    orgId: data.org_id,
    orgName: data.org_name,
    plan: data.plan,
    subscriptionStatus: data.subscription_status,
    signupAt: data.signup_at,
    locale: data.locale,
    isImpersonating: data.is_impersonating,
  };
}
