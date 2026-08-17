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
  marketing: false,
  publicApi: false,
  enterpriseSso: false,
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
    marketing: modules.marketing,
    publicApi: modules.public_api,
    enterpriseSso: modules.enterprise_sso,
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

// T-263: mirrors packages/hooks' useModules.ts GlobalBanner/MaintenanceState.
// Unlike `modules` and `support`, these change while the app is open -- an
// operator raises a banner mid-incident -- which is what POLL_INTERVAL_MS
// below exists for.
export interface GlobalBanner {
  type: "info" | "warning" | "critical";
  message: string;
  dismissible: boolean;
}

export interface MaintenanceState {
  enabled: boolean;
  message: string;
}

const NO_MAINTENANCE: MaintenanceState = { enabled: false, message: "" };

interface ConfigState {
  modules: ActiveModules;
  support: SupportSettings;
  banner: GlobalBanner | null;
  maintenance: MaintenanceState;
}

let state: ConfigState = {
  modules: DENY_BY_DEFAULT,
  support: UNRESOLVED_SUPPORT,
  banner: null,
  maintenance: NO_MAINTENANCE,
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

export function getBannerSnapshot(): GlobalBanner | null {
  return state.banner;
}

export function getMaintenanceSnapshot(): MaintenanceState {
  return state.maintenance;
}

let bootstrapPromise: Promise<void> | null = null;

async function fetchConfig(): Promise<void> {
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
        banner: envelope.data.banner ?? null,
        maintenance: envelope.data.maintenance,
      });
    }
  } catch {
    // Keep the last resolved answer -- DENY_BY_DEFAULT on a first call, the
    // previous response on a refresh. A banner that vanishes because one poll
    // failed is worse than one that is a minute stale.
  }
}

export function bootstrapModules(): Promise<void> {
  bootstrapPromise ??= fetchConfig();
  return bootstrapPromise;
}

/** T-263: re-read /config now, bypassing the one-shot bootstrap. */
export function refreshConfig(): Promise<void> {
  return fetchConfig();
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

// Same 60s as the backend's settings cache TTL and as the web hook's poll --
// polling faster than the value can change just spends battery and requests.
const POLL_INTERVAL_MS = 60_000;

// One timer for the whole page, not one per hook call. `useGlobalBanner()` and
// `useMaintenance()` are mounted together on the app shell (the layout reads
// the maintenance state, the banner component reads the banner), and a timer
// each would mean two /config requests a minute where one answers both. The
// refcount is what lets the interval stop again when the last caller unmounts.
let pollTimer: ReturnType<typeof setInterval> | null = null;
let pollSubscribers = 0;

function usePolledConfig(): void {
  useEffect(() => {
    void bootstrapModules();
    pollSubscribers += 1;
    pollTimer ??= setInterval(() => {
      void refreshConfig();
    }, POLL_INTERVAL_MS);

    return () => {
      pollSubscribers -= 1;
      if (pollSubscribers === 0 && pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };
  }, []);
}

/**
 * T-263's global announcement, or null. Polls, unlike useModules() above: the
 * whole point of putting a banner in ProductSettings rather than in a build is
 * that it reaches an app that is already open -- and on mobile, "already open"
 * can mean an install that will not be updated for weeks.
 */
export function useGlobalBanner(): GlobalBanner | null {
  usePolledConfig();

  return useSyncExternalStore(subscribeConfig, getBannerSnapshot);
}

/** Whether the product is in a maintenance window, and the operator's copy. */
export function useMaintenance(): MaintenanceState {
  usePolledConfig();

  return useSyncExternalStore(subscribeConfig, getMaintenanceSnapshot);
}
