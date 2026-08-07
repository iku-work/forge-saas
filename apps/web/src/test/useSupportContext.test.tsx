import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { useSupportContext } from "@forge/hooks";

import {
  envelope,
  fixtureOrg,
  fixtureSupportContext,
  makeUser,
} from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();
const user = makeUser(1);

function renderSupportHook() {
  const queryClient = new QueryClient();
  return renderHook(() => useSupportContext(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

// Every test here needs the same signed-in session, so they can share one
// file -- useAuth/useOrg cache their bootstrap for the lifetime of a test
// file (see appShellFixtures' note), and only the /me/support-context
// response varies between these cases. The unauthenticated case can't share
// it, and lives in useSupportContextUnauthenticated.test.tsx.
function signedIn(): void {
  server.use(
    http.post(`${API}/api/v1/auth/refresh`, () =>
      HttpResponse.json(envelope({ access_token: "token" })),
    ),
    http.get(`${API}/api/v1/auth/me`, () => HttpResponse.json(envelope(user))),
    http.get(`${API}/api/v1/orgs`, () =>
      HttpResponse.json(envelope([fixtureOrg])),
    ),
  );
}

describe("useSupportContext (T-262)", () => {
  it("returns null until the context has actually resolved, then the mapped fields", async () => {
    signedIn();

    const { result } = renderSupportHook();

    expect(result.current).toBeNull();

    await waitFor(() => {
      expect(result.current).not.toBeNull();
    });
    expect(result.current).toEqual({
      userId: fixtureSupportContext.user_id,
      email: fixtureSupportContext.email,
      fullName: fixtureSupportContext.full_name,
      orgId: fixtureSupportContext.org_id,
      orgName: fixtureSupportContext.org_name,
      plan: fixtureSupportContext.plan,
      subscriptionStatus: fixtureSupportContext.subscription_status,
      signupAt: fixtureSupportContext.signup_at,
      locale: fixtureSupportContext.locale,
      isImpersonating: false,
    });
  });

  it("reports an impersonated session, so a widget can suppress its identity call", async () => {
    signedIn();
    server.use(
      http.get(`${API}/api/v1/me/support-context`, () =>
        HttpResponse.json(
          envelope({ ...fixtureSupportContext, is_impersonating: true }),
        ),
      ),
    );

    const { result } = renderSupportHook();

    await waitFor(() => {
      expect(result.current?.isImpersonating).toBe(true);
    });
  });

  it("stays null when the context request fails, rather than half-populating one", async () => {
    signedIn();
    server.use(
      http.get(`${API}/api/v1/me/support-context`, () =>
        HttpResponse.json(envelope(null), { status: 500 }),
      ),
    );

    const { result } = renderSupportHook();

    await waitFor(() => {
      expect(result.current).toBeNull();
    });
  });
});
