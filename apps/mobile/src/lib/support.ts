/**
 * T-262: mobile's counterpart to packages/hooks' useSupportContext.
 * Reimplemented here rather than reused for the same reason ./config.ts is:
 * that hook calls @forge/api-client's browser-wired default client, which
 * doesn't exist in this runtime.
 *
 * A phone can't run a JavaScript support widget, so the default here is the
 * one channel that always works: open SUPPORT_URL -- a `mailto:`, prefilled
 * with the user's account context (@forge/utils' supportMailtoUrl), unless
 * the product pointed SUPPORT_URL at a help centre. A product using a vendor
 * with a native SDK registers its own handler via setSupportHandler below;
 * that is mobile's equivalent of web's `forge:support-open` event.
 */
import { Linking } from "react-native";

import { getSupportContextApiV1MeSupportContextGet } from "@forge/api-client";
import { supportMailtoUrl } from "@forge/utils";

import { client } from "./api";
import {
  getSupportSettingsSnapshot,
  bootstrapModules,
  type SupportSettings,
} from "./config";
import { ensureCurrentOrg } from "./org";

// Same camelCase shape @forge/hooks' SupportContext exposes on web -- a
// product wiring both surfaces to one support tool should not have to map
// two different field-name conventions.
export interface SupportContext {
  userId: string;
  email: string;
  fullName: string | null;
  orgId: string;
  orgName: string;
  plan: string | null;
  subscriptionStatus: string | null;
  signupAt: string;
  locale: string;
  isImpersonating: boolean;
}

export type SupportHandler = (
  context: SupportContext | null,
  settings: SupportSettings,
) => boolean | Promise<boolean>;

let handler: SupportHandler | null = null;

/**
 * Product-side hook for a support vendor with a native SDK: return true to
 * say "handled" and suppress the SUPPORT_URL fallback, false to let it run
 * anyway (e.g. the SDK failed to initialise). See
 * docs/runbooks/support-tool-integration.md.
 */
export function setSupportHandler(next: SupportHandler | null): void {
  handler = next;
}

export async function fetchSupportContext(): Promise<SupportContext | null> {
  // The endpoint is org-scoped (X-Org-ID, see get_current_org) and a
  // settings screen is reachable before anything else has pinned an org.
  await ensureCurrentOrg();
  const { data: envelope } = await getSupportContextApiV1MeSupportContextGet({
    client,
    throwOnError: true,
  });
  const data = envelope.data;
  if (!data) {
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

/**
 * Opens support for the signed-in user. Resolves to false when there is
 * nowhere to open (no resolved SUPPORT_URL) or the device refuses the URL,
 * so the caller can show an error instead of silently doing nothing.
 *
 * A failed context fetch is NOT fatal: a user who can't reach the API is
 * exactly the user who most needs to contact support, so the link still
 * opens -- just without the account details attached.
 */
export async function openSupport(subject?: string): Promise<boolean> {
  await bootstrapModules();
  const settings = getSupportSettingsSnapshot();
  if (!settings.url) {
    return false;
  }

  const context = await fetchSupportContext().catch(() => null);

  if (handler) {
    const handled = await handler(context, settings);
    if (handled) {
      return true;
    }
  }

  const url = supportMailtoUrl(settings.url, context, { subject });
  if (!(await Linking.canOpenURL(url))) {
    // No mail client on the device (common on a bare simulator), or a
    // scheme the OS won't route.
    return false;
  }
  await Linking.openURL(url);
  return true;
}
