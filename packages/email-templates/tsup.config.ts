import { defineConfig } from "tsup";

export default defineConfig([
  {
    entry: ["src/index.ts"],
    format: ["esm"],
    dts: true,
    sourcemap: true,
    clean: true,
  },
  {
    // cli.js is invoked by app/workers/jobs/email.py via a bare `node` binary
    // -- the Python worker image ships no node_modules and no pnpm, so every
    // dependency (react, @react-email/*, @forge/i18n's catalogs) must be
    // inlined into this one file rather than left as an external import.
    entry: { cli: "src/cli.ts" },
    format: ["esm"],
    platform: "node",
    noExternal: [/.*/],
    splitting: false,
    dts: false,
    sourcemap: false,
    clean: false,
    // A bundled dependency (transitively, inside react-dom/server's stream
    // renderer) calls require() with a computed module name, which esbuild
    // can't inline -- it leaves a bare `require(...)` call in the output,
    // and plain ESM has no global `require` to satisfy it at runtime. This
    // shim restores one, scoped to this file, via Node's own module API.
    esbuildOptions(options) {
      options.banner = {
        js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);",
      };
    },
  },
]);
