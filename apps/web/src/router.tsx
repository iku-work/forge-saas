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
