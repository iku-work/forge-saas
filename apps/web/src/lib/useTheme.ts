import { useCallback, useEffect, useSyncExternalStore } from "react";

import { updateMeApiV1AuthMePatch } from "@forge/api-client";
import { useAuth } from "@forge/hooks";

import {
  getPreference,
  getResolvedTheme,
  reconcileServerPreference,
  setPreference,
  subscribeTheme,
  type ResolvedTheme,
  type ThemePreference,
} from "./theme";

export interface UseThemeResult {
  /** What the user chose. This is what a control should render as selected. */
  preference: ThemePreference;
  /** What is actually painted right now -- "system" already resolved. */
  resolved: ResolvedTheme;
  setTheme: (next: ThemePreference) => Promise<void>;
}

/**
 * F-004. Shaped after useLocale() in @forge/hooks: apply locally first, then
 * persist, and treat a failed persist as "this device only" rather than as an
 * error the user has to acknowledge.
 *
 * Lives in apps/web rather than @forge/hooks because every part of it that is
 * not the PATCH is DOM-bound -- `document.documentElement`, `matchMedia`,
 * `BroadcastChannel`. @forge/hooks is imported by apps/mobile, where none of
 * those exist.
 */
export function useTheme(): UseThemeResult {
  const preference = useSyncExternalStore(subscribeTheme, getPreference);
  const resolved = useSyncExternalStore(subscribeTheme, getResolvedTheme);

  const setTheme = useCallback(async (next: ThemePreference): Promise<void> => {
    // Local first, unconditionally. A theme toggle that waits for a round
    // trip before anything changes is the single most obvious place in an app
    // to feel slow, and there is nothing to roll back: the preference is a
    // display choice, not a fact about the account that could be rejected.
    setPreference(next);
    try {
      await updateMeApiV1AuthMePatch({
        body: { theme: next },
        throwOnError: true,
      });
    } catch {
      // Signed out, offline, or the request failed. The choice is applied and
      // stored on this device; it just will not follow the user to another
      // one yet. Same reasoning as useLocale's setLocale.
    }
  }, []);

  return { preference, resolved, setTheme };
}

/**
 * Applies the signed-in user's saved theme, once their profile is known.
 *
 * Separate from useTheme() because it must run exactly once per session, at
 * the root -- not in every component that renders a toggle. Mounting it twice
 * would be harmless but pointless; mounting it inside the toggle would mean
 * the server's preference only arrived on pages that happen to show one.
 *
 * The local preference is applied first (by startThemeSync, from
 * localStorage), so the common case -- same device, unchanged choice -- never
 * repaints: adoptServerPreference() short-circuits when the value matches.
 * An account still on its default "system" does not overrule a choice made
 * on this device; that choice is saved to the account instead (see
 * reconcileServerPreference).
 */
export function useServerThemeSync(): void {
  const { user } = useAuth();
  const theme = user?.theme;

  useEffect(() => {
    if (theme === "light" || theme === "dark" || theme === "system") {
      const keep = reconcileServerPreference(theme);
      if (keep) {
        // Fire and forget, like setTheme: if it fails the choice still holds
        // on this device and is offered again on the next sign-in.
        void updateMeApiV1AuthMePatch({ body: { theme: keep } });
      }
    }
  }, [theme]);
}
