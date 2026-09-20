import type { QueryClient } from "@tanstack/react-query";
import { createRouter, type RouterHistory } from "@tanstack/react-router";

import { NotFoundPage } from "./layouts/NotFoundPage";
import { routeTree } from "./routeTree.gen";
import type { AuthContext } from "./routes/__root";

export function createAppRouter(
  queryClient: QueryClient,
  history?: RouterHistory,
) {
  return createRouter({
    routeTree,
    context: {
      auth: { user: null } satisfies AuthContext,
      queryClient,
    },
    defaultPreload: "intent",
    // F-006: cross-fade between routes via the View Transitions API, as
    // progressive enhancement. Router core feature-detects it
    // (`"startViewTransition" in document && typeof ... === "function"`), so
    // Firefox and jsdom simply navigate instantly with no error and no
    // branch needed here. The animation itself -- duration, and the
    // prefers-reduced-motion opt-out, which the global `*` rule in
    // tokens.css cannot reach because ::view-transition pseudo-elements are
    // not descendants of anything -- is in apps/web/src/index.css.
    defaultViewTransition: true,
    // T-241: back-navigation returns to where the user was, both for the
    // window and for any element carrying a data-scroll-restoration-id
    // (see lib/scrollRestorationIds.ts). Router core snapshots those
    // offsets per history key into sessionStorage on every navigation.
    scrollRestoration: true,
    defaultNotFoundComponent: NotFoundPage,
    ...(history ? { history } : {}),
  });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
