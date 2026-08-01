import { createFileRoute, notFound, Outlet } from "@tanstack/react-router";

import { isSupportedLocale } from "@forge/i18n";

import { detectPreferredLocale, setUrlLocale } from "../i18n";
import { LocaleHead } from "../lib/LocaleHead";
import { isLocaleShaped, localeRedirect } from "../lib/locale-url";

/**
 * T-233: the layout route every other route now hangs off, so the active
 * locale is in the URL (`/en/settings/profile`) and pages are shareable,
 * bookmarkable and indexable per language.
 *
 * `$locale` is a plain path param, so it matches *any* single segment --
 * which means this `beforeLoad` also sees the two non-locale cases, and has
 * to tell them apart (see isLocaleShaped in lib/locale-url.ts):
 *
 *   /de/settings/profile  -> supported          -> render in German
 *   /xx/settings/profile  -> locale-shaped      -> 404
 *   /login                -> not locale-shaped  -> redirect to /<resolved>/login
 *
 * The last case is what keeps every pre-T-233 bookmark, email link and
 * external inbound link working. Deeper locale-less paths (`/settings/org`)
 * never reach here at all -- they fail to match this subtree and land on the
 * root splat route in `$.tsx`, which applies the same rule.
 */
export const Route = createFileRoute("/$locale")({
  beforeLoad: async ({ context, location, params }) => {
    const { locale } = params;
    if (!isSupportedLocale(locale)) {
      if (isLocaleShaped(locale)) {
        throw notFound();
      }
      localeRedirect(
        location.href,
        detectPreferredLocale(context.auth.user?.locale),
      );
    }
    // The URL wins over the stored preference for *rendering* -- a shared
    // /de/... link shows German even to an `en` user -- but deliberately
    // doesn't write that locale back to localStorage or User.locale. Only an
    // explicit switch does (see lib/RoutedLocaleSwitcher.tsx).
    await setUrlLocale(locale);
    return { locale };
  },
  component: LocaleLayout,
});

function LocaleLayout() {
  return (
    <>
      <LocaleHead />
      <Outlet />
    </>
  );
}
