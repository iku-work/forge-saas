import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  buildExportRows,
  computeImportedNamespaces,
  EN_LOCALE,
  namespacesOf,
  renderNamespaceFile,
} from "./i18n-transfer";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const LOCALES_DIR = join(SCRIPT_DIR, "..", "locales");

export interface RoundtripReport {
  diffs: string[];
}

// T-232's "export/import round-trip is lossless" CI gate. Simulates
// `i18n:export --locale en` followed by `i18n:import` entirely in memory
// (no CSV touches disk) and asserts every locales/en/*.json file comes back
// byte-identical. Runs against `en` specifically because it's the one locale
// guaranteed to have every key non-empty (T-230's check-locales already
// enforces that) -- any other locale could legitimately have missing keys,
// which this check isn't testing.
export function checkRoundtrip(localesDir: string = LOCALES_DIR): RoundtripReport {
  const rows = buildExportRows(localesDir, EN_LOCALE);
  const { namespaces, unknownKeys } = computeImportedNamespaces(localesDir, EN_LOCALE, rows);

  const diffs: string[] = [];
  if (unknownKeys.length > 0) {
    diffs.push(`unexpected unknown key(s) during en self round-trip: ${unknownKeys.join(", ")}`);
  }

  for (const namespace of namespacesOf(localesDir)) {
    const original = readFileSync(join(localesDir, EN_LOCALE, `${namespace}.json`), "utf-8");
    const rebuilt = renderNamespaceFile(namespaces.get(namespace) ?? {});
    if (original !== rebuilt) {
      diffs.push(`locales/en/${namespace}.json changed after an export -> import round-trip`);
    }
  }

  return { diffs };
}

function main(): void {
  const report = checkRoundtrip();
  if (report.diffs.length > 0) {
    for (const diff of report.diffs) {
      console.error(`@forge/i18n: ${diff}`);
    }
    process.exitCode = 1;
    return;
  }
  // eslint-disable-next-line no-console
  console.log("@forge/i18n: check-roundtrip passed (en export -> import is lossless)");
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main();
}
