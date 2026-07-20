import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { renderNamespaceFile } from "./i18n-transfer";
import { parseExportArgs, runExport } from "./i18n-export";

let localesDir: string;
let outputDir: string;

beforeEach(() => {
  const root = mkdtempSync(join(tmpdir(), "forge-i18n-export-"));
  localesDir = join(root, "locales");
  outputDir = join(root, "i18n-export");
  mkdirSync(join(localesDir, "en"), { recursive: true });
  mkdirSync(join(localesDir, "de"), { recursive: true });
  mkdirSync(join(localesDir, "fr"), { recursive: true });
  writeFileSync(join(localesDir, "en", "common.json"), renderNamespaceFile({ greeting: "Hello" }));
  writeFileSync(join(localesDir, "de", "common.json"), renderNamespaceFile({ greeting: "Hallo" }));
  writeFileSync(join(localesDir, "fr", "common.json"), renderNamespaceFile({}));
});

afterEach(() => {
  rmSync(join(localesDir, ".."), { recursive: true, force: true });
});

describe("parseExportArgs", () => {
  it("defaults to no locale filter and missingOnly=false", () => {
    expect(parseExportArgs([])).toEqual({ locale: undefined, missingOnly: false });
  });

  it("reads --locale and --missing-only", () => {
    expect(parseExportArgs(["--locale", "de", "--missing-only"])).toEqual({
      locale: "de",
      missingOnly: true,
    });
  });

  it("rejects an unknown flag", () => {
    expect(() => parseExportArgs(["--bogus"])).toThrow(/unknown i18n:export argument/);
  });

  it("rejects --locale with no value", () => {
    expect(() => parseExportArgs(["--locale"])).toThrow(/requires a value/);
  });
});

describe("runExport", () => {
  it("with no --locale, writes a csv+po pair for every non-en locale", () => {
    const written = runExport(localesDir, outputDir, { missingOnly: false });
    expect(written.sort()).toEqual(
      [
        join(outputDir, "de.csv"),
        join(outputDir, "de.po"),
        join(outputDir, "fr.csv"),
        join(outputDir, "fr.po"),
      ].sort(),
    );
    expect(existsSync(join(outputDir, "en.csv"))).toBe(false);
  });

  it("with --locale, writes only that locale's pair", () => {
    const written = runExport(localesDir, outputDir, { locale: "de", missingOnly: false });
    expect(written).toEqual([join(outputDir, "de.csv"), join(outputDir, "de.po")]);
    const csv = readFileSync(join(outputDir, "de.csv"), "utf-8");
    expect(csv).toContain("common.greeting,Hello,Hallo,common");
  });
});
