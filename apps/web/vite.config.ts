import { sentryVitePlugin } from "@sentry/vite-plugin";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { visualizer } from "rollup-plugin-visualizer";
import { defineConfig } from "vite";

// ANALYZE=true (the `build:analyze` script, T-242) emits dist/stats.html so a
// bundle-budget failure is diagnosable straight from the CI artifact without
// reproducing locally. An env flag rather than a CLI flag because vite's cac
// parser hard-errors on unknown options like `--analyze`.
const analyze = process.env.ANALYZE === "true";

export default defineConfig({
  // tanstackRouter must run before react() — it generates routeTree.gen.ts
  // and splits each route's component into its own chunk automatically.
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
    // T-201: uploads source maps for whatever it builds so Sentry can
    // de-minify web stack traces -- only runs when CI provides an auth
    // token; a local `vite build` never has one, so the plugin no-ops
    // (SENTRY_ORG/SENTRY_PROJECT are similarly CI-only).
    ...(process.env.SENTRY_AUTH_TOKEN
      ? [
          sentryVitePlugin({
            authToken: process.env.SENTRY_AUTH_TOKEN,
            org: process.env.SENTRY_ORG,
            project: process.env.SENTRY_PROJECT,
            release: { name: process.env.VITE_SENTRY_RELEASE },
          }),
        ]
      : []),
    ...(analyze
      ? [
          visualizer({
            filename: "dist/stats.html",
            gzipSize: true,
            brotliSize: true,
            template: "treemap",
          }),
        ]
      : []),
  ],
  // @forge/api-client's getApiBaseUrl() reads this instead of
  // import.meta.env.VITE_API_URL -- see that file's comment for why
  // (import.meta is unusable in the same package's Metro/Hermes consumers).
  define: {
    __VITE_API_URL_OVERRIDE__: JSON.stringify(process.env.VITE_API_URL),
  },
  // Sentry needs these to de-minify stack traces; harmless when nothing
  // ever uploads them (sentryVitePlugin above is CI-only). target is bumped
  // past Vite's default (~es2020) because main.tsx's top-level `await
  // initI18n()` needs it -- default esbuild target predates broad top-level-
  // await support (Chrome/Edge/Firefox 89+, Safari 15+, all from 2021).
  build: {
    sourcemap: true,
    target: "es2022",
    rollupOptions: {
      output: {
        // Stable, distinctive name for the always-loaded entry chunk so the
        // T-242 size-limit budget can target `entry-*.js` unambiguously.
        // rollup's default fallback name for this chunk is "index", which it
        // also hands to the anonymous shared vendor chunks; renaming only the
        // entry leaves those as `index-*.js` (budgeted as a group) and, unlike
        // a manualChunks split, doesn't perturb the actual chunk graph.
        entryFileNames: "assets/entry-[hash].js",
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
