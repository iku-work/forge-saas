import { sentryVitePlugin } from "@sentry/vite-plugin";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

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
  ],
  // @forge/api-client's getApiBaseUrl() reads this instead of
  // import.meta.env.VITE_API_URL -- see that file's comment for why
  // (import.meta is unusable in the same package's Metro/Hermes consumers).
  define: {
    __VITE_API_URL_OVERRIDE__: JSON.stringify(process.env.VITE_API_URL),
  },
  // Sentry needs these to de-minify stack traces; harmless when nothing
  // ever uploads them (sentryVitePlugin above is CI-only).
  build: {
    sourcemap: true,
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
