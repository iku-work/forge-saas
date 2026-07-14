import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const LOCALES_DIR = join(SCRIPT_DIR, "..", "locales");
const EN_LOCALE = "en";

// Locales that are intentionally shipped incomplete as a worked example of
// graceful fallback (see packages/i18n/README.md and agents.md T-067) --
// exempt from the coverage gate below, but still held to the orphan-key and
// non-empty-en-value checks like every other locale. "fi" is the only member
// today; a locale only belongs here if its incompleteness is deliberate and
// documented, not a stalled translation effort.
export const PARTIAL_LOCALES = new Set(["fi"]);

// T-230: fail if more than this fraction of `en` keys are missing from a
// non-partial locale.
const MAX_MISSING_RATIO = 0.05;

type JsonObject = Record<string, unknown>;

function flatten(node: JsonObject, prefix: string, out: Map<string, string>): void {
  for (const [segment, value] of Object.entries(node)) {
    const path = prefix ? `${prefix}.${segment}` : segment;
    if (typeof value === "string") {
      out.set(path, value);
    } else if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      flatten(value as JsonObject, path, out);
    } else {
      throw new Error(`@forge/i18n: leaf value at "${path}" must be a string, got ${typeof value}`);
    }
  }
}

// Reads every `<namespace>.json` file directly under `localeDir` (skipping
// non-namespace files like a locale's `__review.json` marker) and flattens
// them into a single dotted-key -> value map, namespaced the same way
// src/index.ts's `t()` resolves keys ("auth.login.email_label").
export function collectKeyValues(localeDir: string): Map<string, string> {
  const out = new Map<string, string>();
  const files = readdirSync(localeDir).filter(
    (file) => file.endsWith(".json") && !file.startsWith("__"),
  );
  for (const file of files) {
    const namespace = file.replace(/\.json$/, "");
    const content = JSON.parse(readFileSync(join(localeDir, file), "utf-8")) as JsonObject;
    flatten(content, namespace, out);
  }
  return out;
}

export interface LocaleCheckResult {
  locale: string;
  missingKeys: string[];
  orphanKeys: string[];
  missingRatio: number;
}

// Pure comparison, independent of the filesystem, so tests can exercise it
// against synthetic key sets without touching real locale files.
export function compareLocale(
  locale: string,
  enKeys: ReadonlySet<string>,
  localeValues: ReadonlyMap<string, string>,
): LocaleCheckResult {
  const localeKeys = new Set(localeValues.keys());
  const missingKeys = [...enKeys].filter((key) => !localeKeys.has(key)).sort();
  const orphanKeys = [...localeKeys].filter((key) => !enKeys.has(key)).sort();
  return {
    locale,
    missingKeys,
    orphanKeys,
    missingRatio: enKeys.size === 0 ? 0 : missingKeys.length / enKeys.size,
  };
}

export interface CheckLocalesReport {
  emptyEnKeys: string[];
  results: LocaleCheckResult[];
  errors: string[];
}

// Locale codes are the LOCALES_DIR's own subdirectories rather than
// SUPPORTED_LOCALES imported from src/index.ts: this script has to run
// against locales/ before `pnpm build` has produced anything importable, and
// a locale can exist on disk (mid-translation, not yet wired into
// SUPPORTED_LOCALES) that this check should still validate.
export function checkLocales(localesDir: string = LOCALES_DIR): CheckLocalesReport {
  const localeCodes = readdirSync(localesDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();

  const enValues = collectKeyValues(join(localesDir, EN_LOCALE));
  const enKeys = new Set(enValues.keys());
  const emptyEnKeys = [...enValues.entries()]
    .filter(([, value]) => value.trim() === "")
    .map(([key]) => key)
    .sort();

  const results: LocaleCheckResult[] = [];
  const errors: string[] = [];

  if (emptyEnKeys.length > 0) {
    errors.push(
      `locales/en has ${emptyEnKeys.length} empty value(s): ${emptyEnKeys.join(", ")}`,
    );
  }

  for (const locale of localeCodes) {
    if (locale === EN_LOCALE) continue;
    const values = collectKeyValues(join(localesDir, locale));
    const result = compareLocale(locale, enKeys, values);
    results.push(result);

    if (result.orphanKeys.length > 0) {
      errors.push(
        `locales/${locale} has ${result.orphanKeys.length} orphan key(s) absent from en: ${result.orphanKeys.join(", ")}`,
      );
    }
    if (!PARTIAL_LOCALES.has(locale) && result.missingRatio > MAX_MISSING_RATIO) {
      errors.push(
        `locales/${locale} is missing ${result.missingKeys.length}/${enKeys.size} en keys ` +
          `(${(result.missingRatio * 100).toFixed(1)}% > ${MAX_MISSING_RATIO * 100}% threshold)`,
      );
    }
  }

  return { emptyEnKeys, results, errors };
}

function main(): void {
  const report = checkLocales();
  for (const result of report.results) {
    const partial = PARTIAL_LOCALES.has(result.locale) ? " (partial, exempt)" : "";
    // eslint-disable-next-line no-console
    console.log(
      `@forge/i18n: ${result.locale}${partial} -- ${(100 - result.missingRatio * 100).toFixed(1)}% coverage, ` +
        `${result.missingKeys.length} missing, ${result.orphanKeys.length} orphan`,
    );
  }

  if (report.errors.length > 0) {
    for (const error of report.errors) {
      console.error(`@forge/i18n: ${error}`);
    }
    process.exitCode = 1;
    return;
  }

  // eslint-disable-next-line no-console
  console.log("@forge/i18n: check-locales passed");
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main();
}
