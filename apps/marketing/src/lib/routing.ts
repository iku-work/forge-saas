/**
 * Where the locale lives in a marketing URL, and nowhere else (T-301, T-305).
 *
 * Every page under `src/pages/[...locale]/` builds once per enabled locale,
 * and every one of them gets its paths from `localeStaticPaths()` and its
 * links from `localePath()`. That concentration is the point: T-305 settled
 * the prefix policy, and this file plus `i18n.routing` in astro.config.mjs
 * are the whole of it.
 *
 * **The policy, which is apps/web's T-233 policy:** every URL carries a
 * locale segment, the default locale's included. `/en/pricing` is English,
 * `/fr/pricing` is French, and `/pricing` is nobody's -- it is a locale-less
 * URL, which resolves to a locale and redirects, exactly as `$.tsx` does in
 * the app. The two apps agree because a user crossing from one to the other
 * must not watch their language disappear and come back, and because a rule
 * that holds on one side of a product and not the other is a rule nobody can
 * state.
 *
 * The one thing a static site cannot copy is *where* that redirect happens.
 * T-233 resolves it in the router, on the first navigation, with the signed-in
 * user's stored preference available; `output: "static"` has no request to
 * hook and no session to read, so `src/components/LocaleRedirect.astro` does
 * it in the page with the inputs a public, cacheable, session-less origin
 * actually has. That component says so where it happens.
 */
import { defaultLocale, enabledLocales } from "./product";

/**
 * The single switch. `true` matches astro.config.mjs's
 * `routing.prefixDefaultLocale: true`; the two are one decision and must move
 * together, which tests/routes.test.ts asserts.
 *
 * It stays a named constant rather than being folded away now that it is
 * settled, because a product that wants the other policy has to change two
 * things and this is one of them -- and because the name is what makes the
 * `localeSegment` below readable as a policy rather than as a redundant
 * identity function.
 */
const DEFAULT_LOCALE_IS_PREFIXED = true;

export { defaultLocale, enabledLocales };

/**
 * The value of the `[...locale]` rest parameter for a locale: a segment, or
 * `undefined` for "no segment at all".
 *
 * A rest parameter is used rather than `[locale]` because it is what let one
 * page file serve both `/pricing` and `/fr/pricing` while the default locale
 * was unprefixed. Under the policy above it always returns a segment -- kept
 * as the one place that knows, so flipping the constant is genuinely the
 * whole change on this side.
 */
export function localeSegment(locale: string): string | undefined {
  return !DEFAULT_LOCALE_IS_PREFIXED && locale === defaultLocale
    ? undefined
    : locale;
}

/**
 * An absolute site path under `locale`: `localePath("fr", "blog")` is
 * `/fr/blog/`, `localePath("en", "blog")` is `/en/blog/`.
 *
 * Trailing slash on purpose. Astro's default `trailingSlash: "ignore"` writes
 * `blog/index.html`, which a static server serves at `/blog/`; linking to
 * `/blog` makes Caddy issue a redirect on every internal click, and T-306's
 * Lighthouse run counts those.
 */
export function localePath(locale: string, path = ""): string {
  const segments = [localeSegment(locale), ...path.split("/")].filter(
    (segment): segment is string => Boolean(segment),
  );
  return segments.length === 0 ? "/" : `/${segments.join("/")}/`;
}

/** One `getStaticPaths()` entry per enabled locale, for a page with no other params. */
export function localeStaticPaths(): {
  params: { locale: string | undefined };
  props: { locale: string };
}[] {
  return enabledLocales.map((locale) => ({
    params: { locale: localeSegment(locale) },
    props: { locale },
  }));
}

/**
 * The locale a site path is already pinned to, or `undefined` for a
 * locale-less one.
 *
 * Only *enabled* locales count, which is the difference between this and
 * apps/web's `localeOf`: the app validates against @forge/i18n's registry
 * because it renders every locale the package ships, while this site only
 * builds the ones `product.config.ts` turns on. A path under a disabled
 * locale is not a locale-prefixed path here -- there is no page behind it.
 */
export function localeOf(path: string): string | undefined {
  const first = path.split("/").filter(Boolean)[0];
  return first !== undefined && enabledLocales.includes(first)
    ? first
    : undefined;
}

/**
 * The same page under a different locale: `/fr/blog/x/` -> `/de/blog/x/`.
 *
 * The counterpart of apps/web's `withLocale` (T-233), and the one way an
 * `hreflang` URL is built here -- <Seo> calls it for every alternate, so an
 * annotation cannot point somewhere the site's own links would not go. A
 * locale-less path is prefixed rather than rewritten, which is what makes
 * `withLocale("/", "fr")` the French home page.
 */
export function withLocale(path: string, locale: string): string {
  const segments = path.split("/").filter(Boolean);
  if (localeOf(path) !== undefined) {
    segments.shift();
  }
  return localePath(locale, segments.join("/"));
}

/**
 * The locale an entry belongs to, and its slug within that locale, from a
 * content-collection id like `en/hello-world` or `en/deploying/caddy`.
 *
 * Returns `undefined` for an entry whose first path segment is not an enabled
 * locale, so a file dropped into `src/content/blog/` without a locale
 * directory is skipped rather than published at a URL nobody meant.
 */
export function splitEntryId(
  id: string,
): { locale: string; slug: string } | undefined {
  const [locale, ...rest] = id.split("/");
  if (!locale || !enabledLocales.includes(locale) || rest.length === 0) {
    return undefined;
  }
  return { locale, slug: rest.join("/") };
}
