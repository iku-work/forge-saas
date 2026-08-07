import { useEffect, useSyncExternalStore } from "react";

import { getConfigApiV1ConfigGet } from "@forge/api-client";
import type { ConfigResponse, ModulesResponse } from "@forge/api-client";
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

// T-262: which support tool the product wired up, and the URL to fall back
// to when there's no widget to open. Deploy-time config like `modules`
// above, from the same /config response -- the *user's* support context is a
// separate, authenticated call (see useSupportContext.ts).
export interface SupportSettings {
  tool: ConfigResponse["support_tool"];
  url: string;
}

// Empty url = "/config hasn't answered yet", which every caller reads as
// "render no support entry at all" -- the deny-by-default equivalent for a
// link whose real destination isn't known yet.
const UNRESOLVED_SUPPORT: SupportSettings = { tool: "none", url: "" };

interface ConfigState {
  modules: ActiveModules;
  support: SupportSettings;
}

// Module-scoped store (not React Context) so every useModules() call site
// reads the same result without a provider component, and so this file
// never needs JSX -- mirrors useAuth.ts/useOrg.ts.
let state: ConfigState = {
  modules: DENY_BY_DEFAULT,
  support: UNRESOLVED_SUPPORT,
};
const listeners = new Set<() => void>();

function setState(next: ConfigState): void {
  state = next;
  for (const listener of listeners) {
    listener();
  }
}

function subscribeConfig(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// Exported so non-component code -- route `beforeLoad` guards (T-064),
// tests -- can check the current modules without calling a hook outside a
// component. Mirrors apps/mobile/src/lib/config.ts's getModulesSnapshot().
export function getModulesSnapshot(): ActiveModules {
  return state.modules;
}

function getSupportSnapshot(): SupportSettings {
  return state.support;
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
        setState({
          modules: toActiveModules(envelope.data.modules),
          support: {
            tool: envelope.data.support_tool,
            url: envelope.data.support_url,
          },
        });
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

  return useSyncExternalStore(subscribeConfig, getModulesSnapshot);
}

// Same one-fetch-per-page-load store as useModules() above -- both read the
// single /config response, so a page using both makes one request, not two.
export function useSupportSettings(): SupportSettings {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeConfig, getSupportSnapshot);
}
