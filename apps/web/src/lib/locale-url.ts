/**
 * T-233: pure helpers for the locale segment that now prefixes every web URL
 * (`/en/settings/profile`, `/fi/login`, ...).
 *
 * Kept free of React, i18next, and the router so the route definitions, the
 * language switcher, the canonical/hreflang head component, and their tests
 * all share one implementation of "where does the locale live in this path".
 */
import { redirect } from "@tanstack/react-router";

import {
  DEFAULT_LOCALE,
  isSupportedLocale,
  type SupportedLocale,
} from "@forge/i18n";

/**
 * A path's first segment is treated as a *locale slot* when it looks like a
 * language tag -- two lowercase letters, optionally with a region subtag.
 *
 * This shape test is what separates the two failure modes the router has to
 * tell apart, because both arrive as `/$locale/...` matches:
 *
 *   /xx/settings   -> locale-shaped but not in the registry -> 404
 *   /login         -> not locale-shaped, so it's a legacy locale-less URL
 *                     -> redirect to /<resolved>/login
 *
 * It holds only because no route in this app starts with a two-letter
 * segment. Adding one (e.g. `/ai/...`) would make it ambiguous with a future
 * locale of the same name -- pick a longer segment instead.
 */
const LOCALE_SHAPED = /^[a-z]{2}(-[A-Za-z0-9]{2,8})?$/;

export function isLocaleShaped(segment: string): boolean {
  return LOCALE_SHAPED.test(segment);
}

interface SplitHref {
  /** Path only, no search or hash, no trailing slash except for "/". */
  pathname: string;
  /** Everything from the first "?" or "#" onwards, "" when neither is present. */
  suffix: string;
}

function splitHref(href: string): SplitHref {
  const cut = href.search(/[?#]/);
  return cut === -1
    ? { pathname: href, suffix: "" }
    : { pathname: href.slice(0, cut), suffix: href.slice(cut) };
}

function segmentsOf(pathname: string): string[] {
  return pathname.split("/").filter((segment) => segment !== "");
}

/**
 * The locale a URL is already pinned to, or `undefined` for a locale-less
 * (legacy) one. Only *supported* locales count -- `/xx/settings` has no
 * locale, it has a 404.
 */
export function localeOf(href: string): SupportedLocale | undefined {
  const first = segmentsOf(splitHref(href).pathname)[0];
  return isSupportedLocale(first) ? first : undefined;
}

/** The same URL with its locale segment stripped: `/de/settings` -> `/settings`. */
export function stripLocale(href: string): string {
  const { pathname, suffix } = splitHref(href);
  const segments = segmentsOf(pathname);
  if (isSupportedLocale(segments[0])) {
    segments.shift();
  }
  return `/${segments.join("/")}${suffix}`;
}

/**
 * The same URL rendered under `locale` -- replacing the existing locale
 * segment when there is one, prefixing when there isn't. Search and hash are
 * carried through untouched, which is what makes the language switcher
 * "never lose the page the user is on" (T-233) rather than bouncing them to
 * the dashboard.
 */
export function withLocale(href: string, locale: string): string {
  const { pathname, suffix } = splitHref(href);
  const segments = segmentsOf(pathname);
  if (isSupportedLocale(segments[0])) {
    segments[0] = locale;
  } else {
    segments.unshift(locale);
  }
  return `/${segments.join("/")}${suffix}`;
}

/**
 * Which locale a locale-less request should land on.
 *
 * Order mirrors the backend's own resolution (see
 * backend/app/core/i18n.py + the LocaleMiddleware): the signed-in user's
 * stored preference first, then the browser's `Accept-Language` equivalent,
 * then the default. `storedLocale` sits between them as the tier the backend
 * has no counterpart for -- an *anonymous* visitor's explicit in-session
 * choice from the language switcher (T-069's localStorage contract), which
 * for a signed-in user is already mirrored into `User.locale` by the switcher
 * itself.
 */
export function resolvePreferredLocale(options: {
  userLocale?: string | null | undefined;
  storedLocale?: string | null | undefined;
  browserLocales?: readonly string[];
}): SupportedLocale {
  const { userLocale, storedLocale, browserLocales = [] } = options;
  if (isSupportedLocale(userLocale)) {
    return userLocale;
  }
  if (isSupportedLocale(storedLocale)) {
    return storedLocale;
  }
  for (const candidate of browserLocales) {
    const base = candidate.split("-")[0] ?? candidate;
    if (isSupportedLocale(base)) {
      return base;
    }
  }
  return DEFAULT_LOCALE;
}

/**
 * The redirect a locale-less URL gets: same path, same query, same hash, now
 * under the resolved locale. `replace: true` so a bookmarked `/settings` and
 * the `/en/settings` it lands on don't become two back-button steps.
 *
 * Callers `throw` the result (TanStack's `beforeLoad` contract). There is no
 * HTTP-level equivalent here on purpose: the web app is a static SPA served
 * by Caddy with a `try_files ... /index.html` fallback (see the repo root
 * Caddyfile), so there is no SSR/edge layer that could issue a real 301 --
 * every request for a locale-less deep link already returns the app shell,
 * and this is the first point at which the resolution inputs (the signed-in
 * user's preference) exist. Products that put this template behind an SSR or
 * edge runtime should move the same rule up to that layer as a 301.
 */
export function localeRedirect(href: string, locale: SupportedLocale): never {
  throw redirect({ href: withLocale(href, locale), replace: true });
}
