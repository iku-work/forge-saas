/**
 * Build-time translation for .astro pages (T-300).
 *
 * The strings come from @forge/i18n -- the same locale JSON apps/web reads,
 * which is what keeps a term from having one meaning on the marketing site
 * and another inside the product (T-305 depends on this staying true).
 *
 * Deliberately *not* an i18next provider. Every island is its own React root,
 * so a provider would mean shipping i18next to a static page and initialising
 * it once per island. Pages translate here, at build time, and pass finished
 * strings to islands as props. An island that genuinely switches language in
 * the browser is the only reason to revisit that, and it needs justifying in
 * review.
 */
import { t as translate } from "@forge/i18n";

export type Translate = (
  key: string,
  params?: Record<string, string | number>,
) => string;

/** A `t()` bound to one locale, so a page passes the locale once. */
export function translator(locale: string): Translate {
  return (key, params = {}) => translate(key, params, locale);
}
