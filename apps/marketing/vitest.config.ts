import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    // Every test here runs a real `astro build` into this app's dist/, so two
    // files running at once would be two builds writing the same directory.
    fileParallelism: false,
    // A cold Astro build plus the Vite client bundle for the island runs
    // ~10-20s on a loaded CI runner; the default 5s timeout is for unit tests.
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
});
