import { useEffect, useSyncExternalStore } from "react";

import { getConfigApiV1ConfigGet } from "@forge/api-client";
import type { ModulesResponse } from "@forge/api-client";
import type { ProductConfig } from "@forge/types";

export type ActiveModules = ProductConfig["modules"];

// Deny-by-default: every optional module reads as off until /config
// resolves, so a slow or failed request never renders a billing/admin/etc.
// surface that the active product config doesn't actually enable.
const DENY_BY_DEFAULT: ActiveModules = {
  billing: false,
  notifications: false,
  fileStorage: false,
  featureFlags: false,
  gdpr: false,
  admin: false,
  oauth: false,
  totp2fa: false,
  multiOrg: false,
  usageMetering: false,
  outboundWebhooks: false,
};

function toActiveModules(modules: ModulesResponse): ActiveModules {
  return {
    billing: modules.billing,
    notifications: modules.notifications,
    fileStorage: modules.file_storage,
    featureFlags: modules.feature_flags,
    gdpr: modules.gdpr,
    admin: modules.admin,
    oauth: modules.oauth,
    totp2fa: modules.totp_2fa,
    multiOrg: modules.multi_org,
    usageMetering: modules.usage_metering,
    outboundWebhooks: modules.outbound_webhooks,
  };
}

// Module-scoped store (not React Context) so every useModules() call site
// reads the same result without a provider component, and so this file
// never needs JSX -- mirrors useAuth.ts/useOrg.ts.
let state: ActiveModules = DENY_BY_DEFAULT;
const listeners = new Set<() => void>();

function setState(next: ActiveModules): void {
  state = next;
  for (const listener of listeners) {
    listener();
  }
}

function subscribeModules(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// Exported so non-component code -- route `beforeLoad` guards (T-064),
// tests -- can check the current modules without calling a hook outside a
// component. Mirrors apps/mobile/src/lib/config.ts's getModulesSnapshot().
export function getModulesSnapshot(): ActiveModules {
  return state;
}

// The active module set never changes at runtime -- it's redeploy-only -- so
// one fetch per page load is cached for every useModules()/route-guard
// caller, same as useAuth's bootstrap(). Exported (as bootstrapModules) so a
// route's `beforeLoad` can await the real result before deciding whether the
// route exists, instead of racing the deny-by-default value a hook would see
// on first render.
let bootstrapPromise: Promise<void> | null = null;

export function bootstrapModules(): Promise<void> {
  bootstrapPromise ??= (async () => {
    try {
      const { data: envelope } = await getConfigApiV1ConfigGet({
        throwOnError: true,
      });
      if (envelope.data) {
        setState(toActiveModules(envelope.data.modules));
      }
    } catch {
      // Stay denied-by-default -- see DENY_BY_DEFAULT above.
    }
  })();
  return bootstrapPromise;
}

export function useModules(): ActiveModules {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeModules, getModulesSnapshot);
}
