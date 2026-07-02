import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";

import type { UserPublic } from "@forge/api-client";

export interface AuthContext {
  user: UserPublic | null;
}

export interface RouterAppContext {
  auth: AuthContext;
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterAppContext>()({
  component: Outlet,
});
