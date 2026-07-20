import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  buildExportRows,
  computeImportedNamespaces,
  parseCsv,
  renderNamespaceFile,
  rowsToCsv,
  rowsToPo,
} from "./i18n-transfer";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "forge-i18n-transfer-"));
  mkdirSync(join(root, "en"));
  mkdirSync(join(root, "de"));
  writeFileSync(
    join(root, "en", "common.json"),
    renderNamespaceFile({
      greeting: "Hello",
      nested: { one: "One", two: "Two" },
    }),
  );
  writeFileSync(
    join(root, "de", "common.json"),
    renderNamespaceFile({
      greeting: "Hallo",
      nested: { one: "Eins" },
    }),
  );
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("buildExportRows", () => {
  it("emits one row per en key with the target locale's current translation", () => {
    const rows = buildExportRows(root, "de");
    expect(rows).toEqual([
      { key: "common.greeting", namespace: "common", en: "Hello", translation: "Hallo" },
      { key: "common.nested.one", namespace: "common", en: "One", translation: "Eins" },
      { key: "common.nested.two", namespace: "common", en: "Two", translation: "" },
    ]);
  });

  it("missingOnly restricts rows to exactly the keys absent from the target locale", () => {
    const rows = buildExportRows(root, "de", { missingOnly: true });
    expect(rows.map((row) => row.key)).toEqual(["common.nested.two"]);
  });

  it("reads a locale with no directory yet as entirely missing", () => {
    const rows = buildExportRows(root, "fr");
    expect(rows.every((row) => row.translation === "")).toBe(true);
  });
});

describe("CSV encode/decode", () => {
  it("round-trips values containing commas, quotes, and newlines", () => {
    const rows = buildExportRows(root, "de");
    rows[0]!.translation = 'Hallo, "Welt"\nzweite Zeile';
    const csv = rowsToCsv(rows);
    const parsed = parseCsv(csv);
    expect(parsed[0]!.translation).toBe('Hallo, "Welt"\nzweite Zeile');
    expect(parsed).toHaveLength(rows.length);
  });

  it("parses the header into record keys", () => {
    const csv = "key,en,translation,namespace\ncommon.greeting,Hello,Hallo,common\n";
    const parsed = parseCsv(csv);
    expect(parsed).toEqual([
      { key: "common.greeting", en: "Hello", translation: "Hallo", namespace: "common" },
    ]);
  });
});

describe("rowsToPo", () => {
  it("emits a msgctxt/msgid/msgstr block per row", () => {
    const rows = buildExportRows(root, "de");
    const po = rowsToPo(rows);
    expect(po).toContain('msgctxt "common.greeting"');
    expect(po).toContain('msgid "Hello"');
    expect(po).toContain('msgstr "Hallo"');
  });
});

describe("computeImportedNamespaces", () => {
  it("applies a new translation while leaving other keys untouched", () => {
    const { namespaces, unknownKeys } = computeImportedNamespaces(root, "de", [
      { key: "common.nested.two", translation: "Zwei" },
    ]);
    expect(unknownKeys).toEqual([]);
    expect(namespaces.get("common")).toEqual({
      greeting: "Hallo",
      nested: { one: "Eins", two: "Zwei" },
    });
  });

  it("leaves an existing value alone when the CSV cell for it is empty", () => {
    const { namespaces } = computeImportedNamespaces(root, "de", [
      { key: "common.greeting", translation: "" },
    ]);
    expect(namespaces.get("common")).toEqual({ greeting: "Hallo", nested: { one: "Eins" } });
  });

  it("does not add a key back that was never present, when its CSV cell is empty", () => {
    const { namespaces } = computeImportedNamespaces(root, "de", [
      { key: "common.nested.two", translation: "" },
    ]);
    expect(namespaces.get("common")).toEqual({ greeting: "Hallo", nested: { one: "Eins" } });
  });

  it("flags a key absent from en as unknown and reports it by name", () => {
    const { unknownKeys, namespaces } = computeImportedNamespaces(root, "de", [
      { key: "common.greeting", translation: "Hallo" },
      { key: "common.bogus_key", translation: "???" },
    ]);
    expect(unknownKeys).toEqual(["common.bogus_key"]);
    // namespaces is still computed (caller decides whether to write it) --
    // it's the presence of unknownKeys that signals "don't write".
    expect(namespaces.get("common")).toBeDefined();
  });

  it("an unmodified full export re-imported reproduces the exact same namespace object", () => {
    const rows = buildExportRows(root, "de");
    const { namespaces, unknownKeys } = computeImportedNamespaces(root, "de", rows);
    expect(unknownKeys).toEqual([]);
    expect(namespaces.get("common")).toEqual({ greeting: "Hallo", nested: { one: "Eins" } });
  });

  it("scaffolds a brand-new locale from en's shape, empty until translations are supplied", () => {
    const { namespaces } = computeImportedNamespaces(root, "fr", [
      { key: "common.greeting", translation: "Bonjour" },
    ]);
    expect(namespaces.get("common")).toEqual({ greeting: "Bonjour" });
  });
});
