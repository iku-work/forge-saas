import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { buildExportRows, EN_LOCALE, listLocaleCodes, rowsToCsv, rowsToPo } from "./i18n-transfer";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const LOCALES_DIR = join(SCRIPT_DIR, "..", "locales");
const OUTPUT_DIR = join(SCRIPT_DIR, "..", "i18n-export");

export interface ExportArgs {
  locale?: string;
  missingOnly: boolean;
}

// `pnpm --filter @forge/i18n run i18n:export [--locale de] [--missing-only]`
export function parseExportArgs(argv: string[]): ExportArgs {
  let locale: string | undefined;
  let missingOnly = false;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--locale") {
      i += 1;
      const value = argv[i];
      if (!value) throw new Error("@forge/i18n: --locale requires a value");
      locale = value;
    } else if (arg === "--missing-only") {
      missingOnly = true;
    } else {
      throw new Error(`@forge/i18n: unknown i18n:export argument "${arg}"`);
    }
  }
  return { locale, missingOnly };
}

function targetLocales(localesDir: string, args: ExportArgs): string[] {
  if (args.locale) return [args.locale];
  return listLocaleCodes(localesDir).filter((code) => code !== EN_LOCALE);
}

// Writes `<outputDir>/<locale>.csv` and `<outputDir>/<locale>.po` for every
// target locale. Returns the paths written so main() can report a count and
// tests can assert on them without touching argv/process.
export function runExport(localesDir: string, outputDir: string, args: ExportArgs): string[] {
  mkdirSync(outputDir, { recursive: true });
  const written: string[] = [];
  for (const locale of targetLocales(localesDir, args)) {
    const rows = buildExportRows(localesDir, locale, { missingOnly: args.missingOnly });
    const csvPath = join(outputDir, `${locale}.csv`);
    const poPath = join(outputDir, `${locale}.po`);
    writeFileSync(csvPath, rowsToCsv(rows), "utf-8");
    writeFileSync(poPath, rowsToPo(rows), "utf-8");
    written.push(csvPath, poPath);
  }
  return written;
}

function main(): void {
  const args = parseExportArgs(process.argv.slice(2));
  const written = runExport(LOCALES_DIR, OUTPUT_DIR, args);
  // eslint-disable-next-line no-console
  console.log(`@forge/i18n: wrote ${written.length} file(s) to i18n-export/`);
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main();
}
