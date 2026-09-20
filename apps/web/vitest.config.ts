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
      // Has to exceed src/test/setup.ts's `asyncUtilTimeout: 10000`, and did
      // not: vitest's default is 5s, so a findBy*/waitFor with a 10s budget
      // was killed by the *test* timeout at 5s and could never spend it.
      // That is why the suite failed ~9 tests per full run while every one of
      // them passed when its file was run alone -- the 10s budget only gets
      // used under the file-level parallelism that makes a render slow in the
      // first place. Raising this does not weaken anything: a genuinely
      // broken render still fails, it just reports the assertion rather than
      // a timeout.
      testTimeout: 15000,
    },
  }),
);
