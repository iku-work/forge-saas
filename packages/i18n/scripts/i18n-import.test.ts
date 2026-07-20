import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { renderNamespaceFile, rowsToCsv, buildExportRows } from "./i18n-transfer";
import { localeFromFilename, parseImportArgs, runImport } from "./i18n-import";

let root: string;
let localesDir: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "forge-i18n-import-"));
  localesDir = join(root, "locales");
  mkdirSync(join(localesDir, "en"), { recursive: true });
  mkdirSync(join(localesDir, "de"), { recursive: true });
  writeFileSync(
    join(localesDir, "en", "common.json"),
    renderNamespaceFile({ greeting: "Hello", farewell: "Bye" }),
  );
  writeFileSync(join(localesDir, "de", "common.json"), renderNamespaceFile({ greeting: "Hallo" }));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("parseImportArgs / localeFromFilename", () => {
  it("requires a file argument", () => {
    expect(() => parseImportArgs([])).toThrow(/usage/);
  });

  it("derives the locale from the csv basename", () => {
    expect(localeFromFilename("i18n-export/de.csv")).toBe("de");
    expect(localeFromFilename("/abs/path/fr.po")).toBe("fr");
  });
});

describe("runImport", () => {
  it("merges a new translation into the locale's namespace file", () => {
    const csvPath = join(root, "de.csv");
    writeFileSync(csvPath, "key,en,translation,namespace\ncommon.farewell,Bye,Tschuess,common\n");

    const outcome = runImport(localesDir, csvPath);

    expect(outcome.unknownKeys).toEqual([]);
    expect(outcome.writtenFiles).toEqual([join(localesDir, "de", "common.json")]);
    const content = JSON.parse(readFileSync(join(localesDir, "de", "common.json"), "utf-8"));
    expect(content).toEqual({ greeting: "Hallo", farewell: "Tschuess" });
  });

  it("refuses to write anything and names the offending key when the csv has an unknown key", () => {
    const csvPath = join(root, "de.csv");
    writeFileSync(
      csvPath,
      "key,en,translation,namespace\ncommon.made_up_key,x,y,common\n",
    );
    const before = readFileSync(join(localesDir, "de", "common.json"), "utf-8");

    const outcome = runImport(localesDir, csvPath);

    expect(outcome.unknownKeys).toEqual(["common.made_up_key"]);
    expect(outcome.writtenFiles).toEqual([]);
    expect(readFileSync(join(localesDir, "de", "common.json"), "utf-8")).toBe(before);
  });

  it("round-trips: exporting de then re-importing unchanged leaves the file byte-identical", () => {
    const rows = buildExportRows(localesDir, "de");
    const csvPath = join(root, "de.csv");
    writeFileSync(csvPath, rowsToCsv(rows));
    const before = readFileSync(join(localesDir, "de", "common.json"), "utf-8");

    const outcome = runImport(localesDir, csvPath);

    expect(outcome.unknownKeys).toEqual([]);
    expect(readFileSync(join(localesDir, "de", "common.json"), "utf-8")).toBe(before);
  });

  it("scaffolds a brand-new locale directory that doesn't exist yet", () => {
    const csvPath = join(root, "fr.csv");
    writeFileSync(csvPath, "key,en,translation,namespace\ncommon.greeting,Hello,Bonjour,common\n");

    const outcome = runImport(localesDir, csvPath);

    expect(outcome.unknownKeys).toEqual([]);
    const content = JSON.parse(readFileSync(join(localesDir, "fr", "common.json"), "utf-8"));
    expect(content).toEqual({ greeting: "Bonjour" });
  });
});
