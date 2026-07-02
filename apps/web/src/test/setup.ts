import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { initI18n } from "../i18n";
import { server } from "./server";

// jsdom doesn't implement scrollTo; the router calls it on every navigation.
window.scrollTo = () => undefined;

// The default 1000ms is tight for this suite's first render in a given test
// file: renderApp() builds the whole router, and the very first call per
// file pays a one-time cost (lazy route chunks, Vite's dep-optimizer warming
// up for newer deps like @tanstack/react-virtual) on top of the actual
// render. Under full file-level parallelism that one-time cost competes for
// CPU with every other worker doing the same thing, which is what pushed
// findBy*/waitFor calls past 1000ms intermittently. Raising the ceiling
// doesn't change what's asserted -- a genuinely broken render still times out.
configure({ asyncUtilTimeout: 10000 });

// vitest.config.ts doesn't set `test.globals: true`, so RTL's automatic
// afterEach cleanup (which detects globals) never registers -- without this,
// each render() in a multi-test file piles onto the previous one's DOM.
afterEach(() => {
  cleanup();
});

// onUnhandledRequest: "error" so a component that fires a request nobody
// declared a handler for fails the test loudly instead of hanging or
// silently hitting the real network.
beforeAll(async () => {
  server.listen({ onUnhandledRequest: "error" });
  // renderApp() (test/render.tsx) renders pages directly, bypassing
  // main.tsx/AppRoot -- this is the one place tests get an initialised
  // i18next. Vitest's per-file module isolation means this runs once per
  // test file, same as every other module-scoped singleton in this suite
  // (see useAuth.ts/useModules.ts's own "test-singleton" notes).
  await initI18n();
});
afterEach(() => {
  server.resetHandlers();
});
afterAll(() => {
  server.close();
});
