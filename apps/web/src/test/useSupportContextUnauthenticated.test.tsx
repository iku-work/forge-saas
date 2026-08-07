import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useAuth, useSupportContext } from "@forge/hooks";

// Its own file (not a case in useSupportContext.test.tsx) because useAuth's
// bootstrap is a module-scoped singleton cached for a whole test file -- a
// signed-out scenario can't share a file with a signed-in one. The default
// MSW handler for /auth/refresh is a 401, which is exactly this case.
describe("useSupportContext when nobody is signed in (T-262)", () => {
  it("returns null, so a support widget never boots with an anonymous identity", async () => {
    const queryClient = new QueryClient();
    const { result } = renderHook(
      () => ({ auth: useAuth(), support: useSupportContext() }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      },
    );

    expect(result.current.support).toBeNull();

    // Still null once auth has finished resolving "no session" -- not just
    // null because the check happened too early.
    await waitFor(() => {
      expect(result.current.auth.isLoading).toBe(false);
    });
    expect(result.current.auth.user).toBeNull();
    expect(result.current.support).toBeNull();
  });
});
