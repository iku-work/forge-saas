// Shared, pure logic for the T-232 translator workflow: reading locale
// namespace files into a common in-memory shape, exporting them to CSV/PO,
// and merging a CSV back into namespace JSON without disturbing keys the
// import doesn't touch. Used by scripts/i18n-export.ts, scripts/i18n-import.ts,
// and scripts/check-roundtrip.ts -- none of those own this logic themselves
// so all three stay consistent about what "a locale's data" means.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export type JsonObject = Record<string, unknown>;

export const EN_LOCALE = "en";

// The namespace files a locale is expected to mirror, derived from
// `locales/en/` (the source of truth) rather than hardcoded -- a namespace
// file added to `en/` is picked up here automatically.
export function namespacesOf(localesDir: string): string[] {
  return readdirSync(join(localesDir, EN_LOCALE))
    .filter((file) => file.endsWith(".json") && !file.startsWith("__"))
    .map((file) => file.replace(/\.json$/, ""))
    .sort();
}

export function listLocaleCodes(localesDir: string): string[] {
  return readdirSync(localesDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();
}

function readJsonObject(path: string): JsonObject {
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as JsonObject;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw error;
  }
}

// Reads every `<namespace>.json` en defines, for `locale`, keyed by
// namespace, preserving nesting and key order exactly as JSON.parse produced
// it. A namespace file that doesn't exist yet for `locale` (brand-new
// locale, not yet scaffolded) reads as `{}` rather than throwing.
export function readNamespaceObjects(localesDir: string, locale: string): Map<string, JsonObject> {
  const dir = join(localesDir, locale);
  const namespaces = new Map<string, JsonObject>();
  for (const namespace of namespacesOf(localesDir)) {
    namespaces.set(namespace, readJsonObject(join(dir, `${namespace}.json`)));
  }
  return namespaces;
}

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

export function flattenNamespaces(namespaces: ReadonlyMap<string, JsonObject>): Map<string, string> {
  const out = new Map<string, string>();
  for (const [namespace, node] of namespaces) {
    flatten(node, namespace, out);
  }
  return out;
}

export interface ExportRow {
  key: string;
  namespace: string;
  en: string;
  translation: string;
}

// One row per `en` key. `translation` is "" when the target locale doesn't
// have the key at all -- re-importing that empty cell leaves the key alone
// rather than writing an empty string into the locale file (see
// computeImportedNamespaces below), which is what makes a full,
// unmodified export -> import round-trip lossless even for partial locales.
export function buildExportRows(
  localesDir: string,
  locale: string,
  options: { missingOnly?: boolean } = {},
): ExportRow[] {
  const enFlat = flattenNamespaces(readNamespaceObjects(localesDir, EN_LOCALE));
  const targetFlat = flattenNamespaces(readNamespaceObjects(localesDir, locale));

  const rows: ExportRow[] = [];
  for (const key of [...enFlat.keys()].sort()) {
    const translation = targetFlat.get(key);
    if (options.missingOnly && translation !== undefined) continue;
    rows.push({
      key,
      namespace: key.split(".")[0] ?? key,
      en: enFlat.get(key) ?? "",
      translation: translation ?? "",
    });
  }
  return rows;
}

export interface ImportResult {
  namespaces: Map<string, JsonObject>;
  unknownKeys: string[];
}

// Rebuilds every namespace object for `locale` by walking `en`'s shape:
// a leaf takes the CSV translation if one was supplied (non-empty), else
// whatever was already on disk for that key, else it's omitted entirely.
// Nothing is ever reordered relative to `en`, which is also each locale's
// own key order today (see packages/i18n/README.md) -- so an unmodified
// re-import reproduces the original file byte-for-byte.
export function computeImportedNamespaces(
  localesDir: string,
  locale: string,
  rows: { key: string; translation: string }[],
): ImportResult {
  const enNamespaces = readNamespaceObjects(localesDir, EN_LOCALE);
  const enKeys = new Set(flattenNamespaces(enNamespaces).keys());
  const existingNamespaces = readNamespaceObjects(localesDir, locale);

  const unknownKeys = rows
    .map((row) => row.key)
    .filter((key) => !enKeys.has(key))
    .sort();

  const csvMap = new Map(rows.map((row) => [row.key, row.translation]));
  const namespaces = new Map<string, JsonObject>();
  for (const [namespace, enNode] of enNamespaces) {
    const existingNode = existingNamespaces.get(namespace) ?? {};
    namespaces.set(namespace, buildNode(enNode, existingNode, csvMap, namespace));
  }
  return { namespaces, unknownKeys };
}

function buildNode(
  enNode: JsonObject,
  existingNode: JsonObject,
  csvMap: ReadonlyMap<string, string>,
  prefix: string,
): JsonObject {
  const result: JsonObject = {};
  for (const [segment, enValue] of Object.entries(enNode)) {
    const path = `${prefix}.${segment}`;
    if (typeof enValue === "string") {
      const csvValue = csvMap.get(path);
      const existingValue =
        typeof existingNode[segment] === "string" ? (existingNode[segment] as string) : undefined;
      if (csvValue) {
        result[segment] = csvValue;
      } else if (existingValue !== undefined) {
        result[segment] = existingValue;
      }
    } else if (enValue !== null && typeof enValue === "object" && !Array.isArray(enValue)) {
      const existingChild =
        existingNode[segment] !== null && typeof existingNode[segment] === "object"
          ? (existingNode[segment] as JsonObject)
          : {};
      const child = buildNode(enValue as JsonObject, existingChild, csvMap, path);
      if (Object.keys(child).length > 0) {
        result[segment] = child;
      }
    }
  }
  return result;
}

export function renderNamespaceFile(namespace: JsonObject): string {
  return `${JSON.stringify(namespace, null, 2)}\n`;
}

// -- CSV ----------------------------------------------------------------
// columns: key, en value, current translation, context/namespace (T-232)

const CSV_HEADER = ["key", "en", "translation", "namespace"];

function csvField(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function rowsToCsv(rows: ExportRow[]): string {
  const lines = [CSV_HEADER.join(",")];
  for (const row of rows) {
    lines.push([row.key, row.en, row.translation, row.namespace].map(csvField).join(","));
  }
  return `${lines.join("\n")}\n`;
}

function parseCsvTable(content: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  let i = 0;
  while (i < content.length) {
    const char = content[i];
    if (inQuotes) {
      if (char === '"') {
        if (content[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += char;
      i += 1;
      continue;
    }
    if (char === '"') {
      inQuotes = true;
      i += 1;
      continue;
    }
    if (char === ",") {
      row.push(field);
      field = "";
      i += 1;
      continue;
    }
    if (char === "\r") {
      i += 1;
      continue;
    }
    if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      i += 1;
      continue;
    }
    field += char;
    i += 1;
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

// Parses a CSV written by rowsToCsv (or any RFC4180-ish CSV with the same
// header names) into one record per data row, keyed by header column name.
export function parseCsv(content: string): Record<string, string>[] {
  const table = parseCsvTable(content);
  const header = table[0];
  if (!header) return [];
  return table.slice(1).map((fields) => {
    const record: Record<string, string> = {};
    header.forEach((column, index) => {
      record[column] = fields[index] ?? "";
    });
    return record;
  });
}

// -- gettext .po (T-232: "also emit gettext .po for tool compatibility") ---

function poEscape(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n");
}

export function rowsToPo(rows: ExportRow[]): string {
  return rows
    .map(
      (row) =>
        `#: ${row.namespace}\nmsgctxt "${poEscape(row.key)}"\nmsgid "${poEscape(row.en)}"\nmsgstr "${poEscape(row.translation)}"\n`,
    )
    .join("\n");
}
