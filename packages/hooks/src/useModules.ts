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

// UX-01: which social sign-in providers actually have credentials on this
// deployment. Deny-by-default in the same sense as `modules` above -- an
// empty list renders no buttons, which is the safe answer both before
// /config resolves and on a product that configured no providers.
//
// A separate signal from `modules.oauth` on purpose: the module being on
// says the *routes* exist, not that Google or GitHub will answer. Rendering
// from the flag is what put two permanently-404ing buttons on the login
// page of every deployment of this template.
const NO_OAUTH_PROVIDERS: readonly string[] = [];

// T-263: the operator-controlled half of /config. Unlike `modules` and
// `support` above -- which are deploy-time and cannot change while the page is
// open -- these three change when someone flips a switch in the admin UI
// mid-session, which is what `POLL_INTERVAL_MS` below exists for.
export interface GlobalBanner {
  type: "info" | "warning" | "critical";
  message: string;
  dismissible: boolean;
}

export interface MaintenanceState {
  enabled: boolean;
  // Operator-written prose, rendered verbatim. Empty when they set the flag
  // without writing any copy -- the caller falls back to its own translated
  // default rather than showing a blank screen.
  message: string;
}

const NO_MAINTENANCE: MaintenanceState = { enabled: false, message: "" };

interface ConfigState {
  modules: ActiveModules;
  support: SupportSettings;
  oauthProviders: readonly string[];
  marketingUrl: string;
  banner: GlobalBanner | null;
  maintenance: MaintenanceState;
  signupDisabled: boolean;
}

// Module-scoped store (not React Context) so every useModules() call site
// reads the same result without a provider component, and so this file
// never needs JSX -- mirrors useAuth.ts/useOrg.ts.
let state: ConfigState = {
  modules: DENY_BY_DEFAULT,
  support: UNRESOLVED_SUPPORT,
  oauthProviders: NO_OAUTH_PROVIDERS,
  marketingUrl: "",
  banner: null,
  maintenance: NO_MAINTENANCE,
  signupDisabled: false,
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

function getOAuthProvidersSnapshot(): readonly string[] {
  return state.oauthProviders;
}

function getMarketingUrlSnapshot(): string {
  return state.marketingUrl;
}

function getBannerSnapshot(): GlobalBanner | null {
  return state.banner;
}

function getMaintenanceSnapshot(): MaintenanceState {
  return state.maintenance;
}

function getSignupDisabledSnapshot(): boolean {
  return state.signupDisabled;
}

// The active module set never changes at runtime -- it's redeploy-only -- so
// one fetch per page load is cached for every useModules()/route-guard
// caller, same as useAuth's bootstrap(). Exported (as bootstrapModules) so a
// route's `beforeLoad` can await the real result before deciding whether the
// route exists, instead of racing the deny-by-default value a hook would see
// on first render.
let bootstrapPromise: Promise<void> | null = null;

async function fetchConfig(): Promise<void> {
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
        oauthProviders: envelope.data.oauth_providers ?? NO_OAUTH_PROVIDERS,
        marketingUrl: envelope.data.marketing_url ?? "",
        banner: envelope.data.banner ?? null,
        maintenance: envelope.data.maintenance,
        signupDisabled: envelope.data.signup_disabled ?? false,
      });
    }
  } catch {
    // Keep whatever we last resolved -- for a first call that is
    // DENY_BY_DEFAULT (see above); for a refresh it is the previous answer,
    // which is a far better thing to render than a banner that vanishes
    // because one poll happened to fail.
  }
}

export function bootstrapModules(): Promise<void> {
  bootstrapPromise ??= fetchConfig();
  return bootstrapPromise;
}

/**
 * Re-read /config now, bypassing the one-shot bootstrap.
 *
 * T-263 made part of this response runtime state, so "fetch once per page
 * load" stopped being enough for the banner and the maintenance flag. This is
 * what `useGlobalBanner` polls with; it is also what an admin page can call
 * after changing a setting so the operator sees their own change without a
 * reload.
 */
export function refreshConfig(): Promise<void> {
  return fetchConfig();
}

export function useModules(): ActiveModules {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeConfig, getModulesSnapshot);
}

/**
 * The social sign-in providers this deployment has credentials for.
 *
 * Render buttons from this, never from `useModules().oauth` -- the module
 * flag says the OAuth routes exist, not that any provider is configured,
 * and the two are different questions with the same answer only by
 * coincidence.
 */
export function useOAuthProviders(): readonly string[] {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeConfig, getOAuthProvidersSnapshot);
}

/**
 * The origin of the marketing site, or "" when there is not one.
 *
 * UX-02: the terms and privacy pages live there, on a different host
 * (T-307), so the app cannot link to them with a relative path and cannot
 * guess the hostname. Empty means the marketing module is off and those
 * pages do not exist -- callers render no link rather than a broken one.
 *
 * Paths under it are locale-prefixed, exactly as the marketing site routes
 * them: `${marketingUrl}/${locale}/terms`.
 */
export function useMarketingUrl(): string {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeConfig, getMarketingUrlSnapshot);
}

// Same one-fetch-per-page-load store as useModules() above -- both read the
// single /config response, so a page using both makes one request, not two.
export function useSupportSettings(): SupportSettings {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeConfig, getSupportSnapshot);
}

// How often the runtime half of /config is re-read. Deliberately the same 60s
// as the backend's own settings cache TTL (and the response's Cache-Control):
// polling faster than the value can change just spends requests, and the
// operator-visible worst case is already documented as "about a minute" in
// docs/runbooks/maintenance-mode.md.
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
 * T-263's global announcement, or null when there is none.
 *
 * Polls, unlike every other hook in this file: an operator raising a banner
 * mid-incident needs it to appear on tabs that are already open, which is the
 * whole point of putting it in ProductSettings rather than in a deploy.
 */
export function useGlobalBanner(): GlobalBanner | null {
  usePolledConfig();

  return useSyncExternalStore(subscribeConfig, getBannerSnapshot);
}

/**
 * Whether the product is in a maintenance window, and the operator's copy for
 * it. Polls for the same reason `useGlobalBanner` does -- and, more
 * importantly, so a client sitting on a maintenance screen notices by itself
 * when the window *ends*.
 */
export function useMaintenance(): MaintenanceState {
  usePolledConfig();

  return useSyncExternalStore(subscribeConfig, getMaintenanceSnapshot);
}

/**
 * Whether self-serve registration is currently closed (T-263's
 * `signup_disabled`). The server refuses `POST /auth/register` with a 403
 * regardless -- this only lets the sign-up page say so up front instead of
 * after the user has filled the form in.
 *
 * Not polled: a visitor sitting on the registration page while an operator
 * closes signups is a rounding error, and the request they submit is refused
 * by the server either way.
 */
export function useSignupDisabled(): boolean {
  useEffect(() => {
    void bootstrapModules();
  }, []);

  return useSyncExternalStore(subscribeConfig, getSignupDisabledSnapshot);
}
