import { createFileRoute } from "@tanstack/react-router";

import { detectPreferredLocale } from "../i18n";
import { localeRedirect } from "../lib/locale-url";

/**
 * T-233: "/" itself is never rendered -- it resolves a locale (signed-in
 * user's preference -> explicit in-session choice -> browser languages ->
 * "en", mirroring the backend's own order) and hands off to `/<locale>/`,
 * which is the real dashboard route.
 *
 * This is also where a signed-in user's `User.locale` gets honoured now that
 * AppRoot no longer reconciles it into i18next by hand: `context.auth.user`
 * is populated before RouterProvider mounts, so the very first navigation
 * already knows their preference.
 */
export const Route = createFileRoute("/")({
  beforeLoad: ({ context }) => {
    localeRedirect("/", detectPreferredLocale(context.auth.user?.locale));
  },
});
