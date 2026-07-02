import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, type RenderResult } from "@testing-library/react";

import type { UserPublic } from "@forge/api-client";

import { createAppRouter, type AppRouter } from "../router";

interface RenderAppOptions {
  initialPath?: string;
  user?: UserPublic | null;
  queryClient?: QueryClient;
}

interface RenderAppResult extends RenderResult {
  router: AppRouter;
  queryClient: QueryClient;
}

// Shared by every test that exercises a page through the real router (the
// auth pages, the auth-guard redirects, and the authenticated app shell) --
// each of those test files used to hand-roll this same
// QueryClientProvider + RouterProvider wiring with only the initial path and
// auth context differing.
export function renderApp(options: RenderAppOptions = {}): RenderAppResult {
  const {
    initialPath = "/",
    user = null,
    queryClient = new QueryClient(),
  } = options;
  const router = createAppRouter(
    queryClient,
    createMemoryHistory({ initialEntries: [initialPath] }),
  );
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} context={{ auth: { user } }} />
    </QueryClientProvider>,
  );
  return { ...result, router, queryClient };
}
