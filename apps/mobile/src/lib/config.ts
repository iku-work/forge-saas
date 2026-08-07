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

import {
  getConfigApiV1ConfigGet,
  type ConfigResponse,
  type ModulesResponse,
} from "@forge/api-client";
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

// T-262: mirrors packages/hooks' useModules.ts SupportSettings. An empty
// url means "/config hasn't answered yet", which callers read as "render no
// support entry" -- the deny-by-default equivalent for a link whose real
// destination isn't known yet.
export interface SupportSettings {
  tool: ConfigResponse["support_tool"];
  url: string;
}

const UNRESOLVED_SUPPORT: SupportSettings = { tool: "none", url: "" };

interface ConfigState {
  modules: ActiveModules;
  support: SupportSettings;
}

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

// Exported for the same reason auth.ts exports getSessionSnapshot(): lets
// non-component code (tests, anything that needs a one-off read) check the
// current modules without calling a hook outside a component.
export function getModulesSnapshot(): ActiveModules {
  return state.modules;
}

export function getSupportSettingsSnapshot(): SupportSettings {
  return state.support;
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

// Same single /config fetch as useModules() above -- a screen using both
// makes one request, not two.
export function useSupportSettings(): SupportSettings {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeConfig, getSupportSettingsSnapshot);
}
