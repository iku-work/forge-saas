import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { initI18n } from "../i18n";
import { server } from "./server";

// jsdom doesn't implement scrollTo; the router calls it on every navigation.
window.scrollTo = () => undefined;

// Nor scrollIntoView, which cmdk calls to keep the highlighted command-palette
// row visible as the list re-filters (F-005). Unstubbed it throws
// "scrollIntoView is not a function" *during render*, so the palette never
// mounts at all and every assertion against it fails as "unable to find
// role=dialog" -- a considerably less obvious symptom than the missing API.
// Assigned unconditionally for the same reason as ResizeObserver below: the
// DOM lib types it as always present, so a nullish guard reads as dead code.
Element.prototype.scrollIntoView = () => undefined;

// jsdom doesn't implement ResizeObserver either, and Radix reaches for it
// through @radix-ui/react-use-size the moment a Checkbox mounts inside a
// Dialog (T-313's create-key form, T-322's event picker) -- an unhandled
// ReferenceError during commit, which React surfaces as a render failure
// rather than as the missing-API it is. A no-op is the right stub: nothing
// in this suite asserts on a resize, and the one place that does need real
// measurements installs its own (test/virtualLayout.ts, which saves and
// restores the descriptor it finds -- i.e. this one).
class NoopResizeObserver implements ResizeObserver {
  observe(): void {
    return;
  }
  unobserve(): void {
    return;
  }
  disconnect(): void {
    return;
  }
}
// Assigned unconditionally rather than with `??=`: the DOM lib types
// `globalThis.ResizeObserver` as always present (jsdom disagrees), so the
// nullish check reads to TypeScript as dead code. The one place that wants a
// *real* one saves and restores whatever it finds here (test/virtualLayout.ts).
globalThis.ResizeObserver = NoopResizeObserver;

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
