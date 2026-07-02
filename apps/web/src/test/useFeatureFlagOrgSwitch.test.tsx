import { act, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { OrganizationPublic } from "@forge/api-client";
import { useFeatureFlag, useOrg } from "@forge/hooks";

import { envelope, fixtureOrg, makeUser } from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();
const user = makeUser(1);

const secondOrg: OrganizationPublic = {
  ...fixtureOrg,
  id: "22222222-2222-2222-2222-999999999999",
  name: "Beta Inc",
  slug: "beta-inc",
};

function renderFlagAndOrg() {
  const queryClient = new QueryClient();
  return renderHook(
    () => ({ flag: useFeatureFlag("advanced_reports"), org: useOrg() }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    },
  );
}

describe("useFeatureFlag on org switch", () => {
  it("refetches the flag map (keyed by org id) when the current org changes", async () => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(envelope([fixtureOrg, secondOrg])),
      ),
      // The two orgs resolve opposite values for the same flag -- the only
      // way `result.current.flag` can flip is a real refetch keyed to the
      // newly-selected org id, not a stale cache hit.
      http.get(`${API}/api/v1/orgs/:orgId/feature-flags`, ({ params }) =>
        HttpResponse.json(
          envelope({
            flags: { advanced_reports: params.orgId === fixtureOrg.id },
          }),
        ),
      ),
    );

    const { result } = renderFlagAndOrg();

    await waitFor(() => {
      expect(result.current.flag).toBe(true);
    });

    act(() => {
      result.current.org.switchOrg(secondOrg.id);
    });

    await waitFor(() => {
      expect(result.current.flag).toBe(false);
    });
  });
});
