/**
 * Reads the repo-root product.config.ts from plain Node (T-300).
 *
 * Two callers need it before Vite exists to compile TypeScript for them:
 * astro.config.mjs, which derives the i18n locale list from it, and
 * guard-enabled.mjs, which decides whether this app builds at all. Inside
 * `src/`, import product.config.ts directly instead -- Astro compiles it.
 */
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createJiti } from "jiti";

export const REPO_ROOT = resolve(fileURLToPath(import.meta.url), "..", "..", "..", "..");

// Overridable by path only -- see guard-enabled.mjs for why a path is a safer
// test seam than a boolean, and why CI never needs it.
export const PRODUCT_CONFIG_PATH =
  process.env["FORGE_PRODUCT_CONFIG"] ?? resolve(REPO_ROOT, "product.config.ts");

/**
 * Loaded through jiti rather than parsed out of the file's text: a regex
 * cannot tell `marketing: true` in the modules block from the same string in
 * a comment or in a future block, which is the argument CI's module-matrix
 * job already makes for reading the Python config instead of grepping this
 * one. jiti also keeps this independent of which Node versions strip
 * TypeScript types without a flag -- package.json only promises >=22.13,
 * where they do not.
 */
export async function loadProductConfig(configPath = PRODUCT_CONFIG_PATH) {
  const jiti = createJiti(import.meta.url, { interopDefault: true });
  const loaded = await jiti.import(configPath);
  const config = loaded.config ?? loaded.default?.config;
  if (!config || typeof config !== "object") {
    throw new Error(`${configPath}: expected a named \`config\` export`);
  }
  return config;
}

/**
 * The locales this product actually exposes, in the registry's order, plus
 * the one that loads before any detection runs. `locales` in product.config.ts
 * is a per-locale toggle map with a `default` key mixed in, so the default is
 * split out here rather than by every caller.
 */
export function enabledLocales(config) {
  const { default: defaultLocale, ...toggles } = config.locales;
  const locales = Object.entries(toggles)
    .filter(([, enabled]) => enabled === true)
    .map(([code]) => code);

  if (!locales.includes(defaultLocale)) {
    throw new Error(
      `product.config.ts: locales.default is "${defaultLocale}", which is not one of the enabled locales (${locales.join(", ")})`,
    );
  }
  return { locales, defaultLocale };
}
