import { configDefaults, defineConfig, mergeConfig } from "vitest/config";

import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      // e2e/ holds Playwright specs (T-220), run via `pnpm e2e`, not
      // vitest -- both frameworks default to picking up *.spec.ts, and
      // Playwright's test() rejects being called outside its own runner.
      exclude: [...configDefaults.exclude, "e2e/**"],
    },
  }),
);
