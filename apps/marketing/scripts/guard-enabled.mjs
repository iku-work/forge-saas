/**
 * The `marketing` module toggle, applied to a *build* (T-290, T-300).
 *
 * Every other module in product.config.ts switches a router or a UI section
 * off inside a running process. This one switches off a whole app: when
 * `modules.marketing` is false there is no marketing site, so `astro build`
 * should not run and `dist/` should not exist. That has to hold from the root
 * `pnpm build`, because that is what CI's module-matrix job runs -- see the
 * "The marketing build honours its toggle" step in .github/workflows/ci.yml,
 * which fails when a `dist/` appears with the toggle off, or fails to appear
 * with it on.
 *
 * The config path is overridable by FORGE_PRODUCT_CONFIG -- by path only, see
 * product-config.mjs. CI never sets it: the module-matrix job copies its
 * fixture over the real path instead, which is the same substitution one
 * level up.
 *
 * Usage: `node scripts/guard-enabled.mjs astro <subcommand> [args...]`
 */
import { spawnSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { removeCaddySites } from "./caddy-site.mjs";
import { loadProductConfig, PRODUCT_CONFIG_PATH } from "./product-config.mjs";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Reads `modules.marketing` out of a product.config.ts. */
export async function readMarketingEnabled(configPath = PRODUCT_CONFIG_PATH) {
  const config = await loadProductConfig(configPath);
  if (typeof config.modules?.marketing !== "boolean") {
    throw new Error(
      `${configPath}: expected a boolean at config.modules.marketing, found ${typeof config
        .modules?.marketing}`,
    );
  }
  return config.modules.marketing;
}

// astro's bin is not reachable through its `exports` map, but its
// package.json is -- resolving that and walking to the bin keeps this a
// plain `node <file>` spawn. Going through `node_modules/.bin/astro` instead
// would mean a shell on Windows (the entry there is a .CMD shim), and
// `shell: true` turns every argument into something the shell parses.
function astroEntrypoint() {
  const require = createRequire(import.meta.url);
  return join(dirname(require.resolve("astro/package.json")), "bin", "astro.mjs");
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command !== "astro") {
    console.error(
      `guard-enabled.mjs: expected \`astro <subcommand>\`, got \`${[command, ...args].join(" ")}\``,
    );
    process.exit(2);
  }

  if (await readMarketingEnabled()) {
    const result = spawnSync(process.execPath, [astroEntrypoint(), ...args], {
      cwd: APP_ROOT,
      stdio: "inherit",
    });
    process.exit(result.status ?? 1);
  }

  // Disabled. Exit 0 -- a product that turned marketing off did not ask for a
  // broken build -- but leave nothing behind that looks like a site: a stale
  // dist/ from before the toggle flipped would be served by T-307's Caddy
  // config and indexed by T-304's sitemap.
  const dist = join(APP_ROOT, "dist");
  if (existsSync(dist)) {
    rmSync(dist, { recursive: true, force: true });
    console.log("marketing is disabled in product.config.ts — removed a stale dist/");
  }

  // And the two Caddy site blocks the build writes (T-307), for the same
  // reason and in the same breath: with the module off, the requirement is
  // that the marketing block is *absent* from the config Caddy loads -- not
  // that it is present and serving an empty directory. The repo-root
  // Caddyfiles import this directory as a glob, so removing the files is all
  // it takes; the app host is untouched either way.
  const removed = await removeCaddySites();
  if (removed.length > 0) {
    console.log(
      `marketing is disabled in product.config.ts — removed ${removed.length} stale Caddy site block(s)`,
    );
  }
  console.log(`marketing is disabled in product.config.ts — skipping \`astro ${args.join(" ")}\``);
}

// Importable for tests (readMarketingEnabled above) without running the CLI.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
