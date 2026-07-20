import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { computeImportedNamespaces, parseCsv, renderNamespaceFile } from "./i18n-transfer";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const LOCALES_DIR = join(SCRIPT_DIR, "..", "locales");

export function parseImportArgs(argv: string[]): { file: string } {
  const file = argv[0];
  if (!file) {
    throw new Error("@forge/i18n: usage: i18n:import <path/to/locale.csv>");
  }
  return { file };
}

// The CSV carries no locale column -- the target locale is the file's own
// basename, matching how i18n:export names its output (`i18n-export/de.csv`
// -> locale "de").
export function localeFromFilename(file: string): string {
  return basename(file).replace(/\.(csv|po)$/i, "");
}

export interface ImportOutcome {
  locale: string;
  writtenFiles: string[];
  unknownKeys: string[];
}

// Pure aside from the write itself: computeImportedNamespaces (i18n-transfer.ts)
// does all the merge logic, this just decides whether to trust it enough to
// touch disk. Refuses to write anything if any row's key isn't in `en`.
export function runImport(localesDir: string, csvPath: string): ImportOutcome {
  const locale = localeFromFilename(csvPath);
  const rows = parseCsv(readFileSync(csvPath, "utf-8"));
  const { namespaces, unknownKeys } = computeImportedNamespaces(localesDir, locale, rows);

  if (unknownKeys.length > 0) {
    return { locale, writtenFiles: [], unknownKeys };
  }

  const localeDir = join(localesDir, locale);
  mkdirSync(localeDir, { recursive: true });
  const writtenFiles: string[] = [];
  for (const [namespace, content] of namespaces) {
    const path = join(localeDir, `${namespace}.json`);
    writeFileSync(path, renderNamespaceFile(content), "utf-8");
    writtenFiles.push(path);
  }
  return { locale, writtenFiles, unknownKeys: [] };
}

function main(): void {
  const { file } = parseImportArgs(process.argv.slice(2));
  const outcome = runImport(LOCALES_DIR, file);

  if (outcome.unknownKeys.length > 0) {
    for (const key of outcome.unknownKeys) {
      console.error(
        `@forge/i18n: unknown key "${key}" (not present in locales/en) -- refusing to import ${outcome.locale}`,
      );
    }
    process.exitCode = 1;
    return;
  }

  // eslint-disable-next-line no-console
  console.log(`@forge/i18n: imported ${outcome.locale} -- wrote ${outcome.writtenFiles.length} file(s)`);
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main();
}
