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

// What this gate is about is *content* -- keys, values, and their order --
// surviving an export/import cycle. Line endings are not content: with git's
// default core.autocrlf on Windows every locale JSON is checked out with
// CRLF, while renderNamespaceFile() (and therefore `i18n:import`) always
// writes LF, so a byte comparison reported all four non-trivial en
// namespaces as "changed" on any Windows checkout while passing in CI. The
// import itself is unaffected -- git normalises both directions back to LF
// in the index -- so normalising here is the fix, not a workaround.
function normalizeEol(content: string): string {
  return content.replace(/\r\n/g, "\n");
}

// T-232's "export/import round-trip is lossless" CI gate. Simulates
// `i18n:export --locale en` followed by `i18n:import` entirely in memory
// (no CSV touches disk) and asserts every locales/en/*.json file comes back
// identical modulo line endings (see normalizeEol). Runs against `en`
// specifically because it's the one locale
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
    const original = normalizeEol(
      readFileSync(join(localesDir, EN_LOCALE, `${namespace}.json`), "utf-8"),
    );
    const rebuilt = normalizeEol(renderNamespaceFile(namespaces.get(namespace) ?? {}));
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
