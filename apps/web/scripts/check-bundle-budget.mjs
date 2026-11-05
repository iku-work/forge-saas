/**
 * Bundle growth is a reviewed decision, not an accident found in a Lighthouse
 * report months later (T-242). What to do when this fails is in
 * docs/runbooks/bundle-budget.md.
 *
 *     node scripts/check-bundle-budget.mjs            # build, compare, fail if over
 *     node scripts/check-bundle-budget.mjs --update   # build, set budgets to size + 10%
 *
 * It measures what a visitor downloads, gzipped:
 *
 *   - "initial JS": everything index.html loads before any route.
 *   - one line per route: what opening it downloads beyond the initial JS,
 *     including the shared chunks it pulls in.
 *   - "largest locale catalog": one cap for all 54 language files.
 *   - "CSS": all stylesheets.
 *
 * Not size-limit, which T-242's spec named: it only sums files matched by a
 * glob, and rollup's hashed `index-*` names say nothing about which route a
 * chunk belongs to. The first attempt needed 230 lines of globs and a separate
 * check that they covered every chunk. Vite's manifest has the real graph, so
 * a new route or lazy import fails here until it has a budget.
 *
 * rollup-plugin-visualizer is the one dependency: its stats.html is what makes
 * a failure diagnosable from the CI artifact.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BUDGETS_FILE = join(APP_ROOT, "bundle-budgets.json");
// Inside dist/ so git, eslint and prettier already ignore it. Never the real
// dist/ itself: the manifest and stats.html must not be served to visitors.
const OUT_DIR = "dist/bundle-check";
const HEADROOM = 1.1;

/** Every chunk a manifest key statically pulls in, itself included. */
function closure(manifest, key, seen = new Set()) {
  if (seen.has(key)) return seen;
  seen.add(key);
  for (const imported of manifest[key].imports ?? [])
    closure(manifest, imported, seen);
  return seen;
}

function displayName(key) {
  return key
    .replace(/^src\/routes\//, "")
    .replace(/\?tsr-split=component$/, "")
    .replace(/-[\w-]{8}\.js$/, ".js"); // A shared chunk's key carries its hash.
}

/** `sizeOf(file)` is passed in so the tests need no build. */
export function measure(manifest, sizeOf) {
  const keys = Object.keys(manifest);
  const entry = keys.find((key) => manifest[key].isEntry);
  const initial = closure(manifest, entry);
  const total = (chunks) =>
    [...chunks].reduce((sum, key) => sum + sizeOf(manifest[key].file), 0);

  const sizes = { "initial JS": total(initial) };
  let largestCatalog = 0;
  for (const key of keys.filter(
    (candidate) => manifest[candidate].isDynamicEntry,
  )) {
    const extra = [...closure(manifest, key)].filter(
      (chunk) => !initial.has(chunk),
    );
    if (extra.length === 0) continue; // Already downloaded, so it costs nothing.
    if (key.includes("/i18n/locales/")) {
      largestCatalog = Math.max(largestCatalog, total(extra));
    } else {
      sizes[displayName(key)] = total(extra);
    }
  }
  sizes["largest locale catalog"] = largestCatalog;

  const stylesheets = new Set(keys.flatMap((key) => manifest[key].css ?? []));
  sizes["CSS"] = [...stylesheets].reduce((sum, file) => sum + sizeOf(file), 0);
  return sizes;
}

/** Everything wrong with `sizes` against `budgets`, as messages. */
export function findProblems(sizes, budgets) {
  const problems = [];
  for (const [name, size] of Object.entries(sizes)) {
    if (!(name in budgets)) problems.push(`${name}: no budget`);
    else if (size > budgets[name])
      problems.push(`${name}: ${size} is over ${budgets[name]}`);
  }
  for (const name of Object.keys(budgets)) {
    if (!(name in sizes))
      problems.push(`${name}: budget for something no longer built`);
  }
  return problems;
}

export function budgetsFor(sizes) {
  return Object.fromEntries(
    Object.entries(sizes)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([name, size]) => [name, Math.ceil(size * HEADROOM)]),
  );
}

function table(sizes, budgets) {
  const width = Math.max(...Object.keys(sizes).map((name) => name.length));
  return Object.entries(sizes)
    .sort(([, a], [, b]) => b - a)
    .map(([name, size]) => {
      const budget = budgets[name];
      const room =
        budget === undefined
          ? "no budget"
          : `${budget - size} left of ${budget}`;
      return `${name.padEnd(width)}  ${String(size).padStart(7)}  ${room}`;
    })
    .join("\n");
}

async function main() {
  const { build } = await import("vite");
  const { visualizer } = await import("rollup-plugin-visualizer");
  const outDir = join(APP_ROOT, OUT_DIR);
  await build({
    root: APP_ROOT,
    logLevel: "warn",
    build: { manifest: true, outDir: OUT_DIR },
    plugins: [
      visualizer({
        filename: join(outDir, "stats.html"),
        gzipSize: true,
        template: "treemap",
      }),
    ],
  });

  const manifest = JSON.parse(
    readFileSync(join(outDir, ".vite", "manifest.json"), "utf8"),
  );
  const sizes = measure(
    manifest,
    (file) => gzipSync(readFileSync(join(outDir, file))).length,
  );

  if (process.argv.includes("--update")) {
    writeFileSync(
      BUDGETS_FILE,
      `${JSON.stringify(budgetsFor(sizes), null, 2)}\n`,
    );
    console.log(
      `Wrote ${BUDGETS_FILE}. Raising a budget needs a reviewer's eye.`,
    );
    return;
  }

  const budgets = JSON.parse(readFileSync(BUDGETS_FILE, "utf8"));
  console.log(table(sizes, budgets));
  const problems = findProblems(sizes, budgets);
  if (problems.length > 0) {
    console.error(`\nBundle budget failed:\n  ${problems.join("\n  ")}`);
    console.error("See docs/runbooks/bundle-budget.md");
    process.exitCode = 1;
  }
}

if (
  process.argv[1] &&
  fileURLToPath(import.meta.url) === resolve(process.argv[1])
) {
  await main();
}
