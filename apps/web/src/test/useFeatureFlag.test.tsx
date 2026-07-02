import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { useFeatureFlag } from "@forge/hooks";

import { envelope, fixtureOrg, makeUser } from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();
const user = makeUser(1);

function renderFlagHook(flag: string) {
  const queryClient = new QueryClient();
  return renderHook(() => useFeatureFlag(flag), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

describe("useFeatureFlag", () => {
  it("defaults to false while the org/flag bootstrap is still loading, then resolves from GET /orgs/:orgId/feature-flags", async () => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(envelope([fixtureOrg])),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/feature-flags`, () =>
        HttpResponse.json(envelope({ flags: { advanced_reports: true } })),
      ),
    );

    const { result } = renderFlagHook("advanced_reports");

    // Deny-by-default: neither the org nor the flag map has resolved yet.
    expect(result.current).toBe(false);

    await waitFor(() => {
      expect(result.current).toBe(true);
    });
  });

  it("returns false for a flag absent from the resolved flag map", async () => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(envelope([fixtureOrg])),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/feature-flags`, () =>
        HttpResponse.json(envelope({ flags: {} })),
      ),
    );

    const { result } = renderFlagHook("advanced_reports");

    await waitFor(() => {
      expect(result.current).toBe(false);
    });
  });
});
