/**
 * Mirrors packages/hooks's useModules.ts (T-063), reimplemented here because
 * that hook calls @forge/api-client's default singleton client, which is
 * wired for the browser (resolves its base URL from Vite's
 * `import.meta.env`) and doesn't exist in this runtime. Same endpoint, same
 * "fetch once per app session, deny-by-default until it resolves" semantics
 * -- see ./api.ts and ./auth.ts for why mobile always reimplements rather
 * than reusing the web hook directly.
 */
import { useEffect, useSyncExternalStore } from "react";

import { getConfigApiV1ConfigGet, type ModulesResponse } from "@forge/api-client";
import type { ProductConfig } from "@forge/types";

import { client } from "./api";

export type ActiveModules = ProductConfig["modules"];

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

// Exported for the same reason auth.ts exports getSessionSnapshot(): lets
// non-component code (tests, anything that needs a one-off read) check the
// current modules without calling a hook outside a component.
export function getModulesSnapshot(): ActiveModules {
  return state;
}

let bootstrapPromise: Promise<void> | null = null;

export function bootstrapModules(): Promise<void> {
  bootstrapPromise ??= (async () => {
    try {
      const { data: envelope } = await getConfigApiV1ConfigGet({
        client,
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
