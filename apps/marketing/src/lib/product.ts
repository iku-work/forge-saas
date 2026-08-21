/**
 * The product's own configuration, read from the file a product edits.
 *
 * apps/marketing is the first frontend consumer of product.config.ts: every
 * tsconfig in the workspace pins `include: ["src"]`, so until now nothing on
 * this side of the stack imported it (the backend has its own mirror in
 * backend/product/config.py, and tests/test_product_config_parity.py proves
 * the two agree). Astro compiles this import like any other, so the file is
 * read as TypeScript rather than parsed.
 *
 * `#product-config` is the repo-root product.config.ts, aliased in
 * astro.config.mjs so this app resolves it in exactly one place -- see the
 * comment on that alias.
 */
import { config } from "#product-config";

export const product = config;
export const modules = config.modules;

/** The locale that loads before any detection runs -- the product's, not @forge/i18n's. */
export const defaultLocale = config.locales.default;

/**
 * Where apps/web is served. T-307 settles the real hosts (marketing at the
 * apex, the app at app.example.com; locally `localhost` stays the app and
 * marketing gets marketing.localhost), which is why the fallback below is
 * this repo's local Caddy host rather than a guess at a production domain.
 */
export const appUrl = import.meta.env.PUBLIC_APP_URL ?? "https://localhost";

/** A path on the app, joined so a trailing slash on PUBLIC_APP_URL cannot double up. */
export function appLink(path: string): string {
  return new URL(path, appUrl.endsWith("/") ? appUrl : `${appUrl}/`).toString();
}
