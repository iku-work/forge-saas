import { act, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { OrganizationPublic } from "@forge/api-client";
import { useOrg } from "@forge/hooks";

import {
  envelope,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();
const user = makeUser(1);

const secondOrg: OrganizationPublic = {
  ...fixtureOrg,
  id: "22222222-2222-2222-2222-999999999999",
  name: "Beta Inc",
  slug: "beta-inc",
};

function renderOrg() {
  const queryClient = new QueryClient();
  return renderHook(() => useOrg(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

function authHandlers() {
  return [
    http.post(`${API}/api/v1/auth/refresh`, () =>
      HttpResponse.json(envelope({ access_token: "token" })),
    ),
    http.get(`${API}/api/v1/auth/me`, () => HttpResponse.json(envelope(user))),
  ];
}

/**
 * useOrg's store is module-scoped and its loads are asynchronous, so every
 * response has to prove it is still the one wanted before it writes. It used
 * to prove nothing: `loadedForUserId` was set before the request went out and
 * never re-read, so whichever response happened to land last won -- which is
 * not the same as the newest.
 *
 * Both tests below make the *older* request the slower one, which is the
 * ordering that turns "usually fine" into a wrong answer on a slow network.
 * The store is shared across this file, so each test waits for the state it
 * depends on rather than assuming a clean one.
 */
describe("useOrg (responses that arrive out of order)", () => {
  beforeEach(() => {
    localStorage.removeItem("forge.currentOrgId");
  });

  it("keeps the newer org list when an earlier, slower load lands last", async () => {
    let orgsCalls = 0;
    server.use(
      ...authHandlers(),
      http.get(`${API}/api/v1/orgs`, async () => {
        orgsCalls += 1;
        if (orgsCalls === 1) {
          // The initial load, overtaken by the refresh below.
          await delay(150);
          return HttpResponse.json(envelope([fixtureOrg]));
        }
        return HttpResponse.json(envelope([fixtureOrg, secondOrg]));
      }),
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
    );

    const { result } = renderOrg();

    // The first load has been sent but cannot have come back yet, so the
    // refresh below genuinely overlaps it.
    await waitFor(() => {
      expect(orgsCalls).toBe(1);
    });
    await act(async () => {
      await result.current.refreshOrgs();
    });

    await waitFor(() => {
      expect(result.current.orgs).toHaveLength(2);
    });

    // Now let the first, slower response resolve. It must recognise that it
    // has been superseded rather than reinstating the one-org list -- the
    // same shape as a logged-out user's in-flight response repopulating the
    // store, or a previous user's orgs landing in the next user's session.
    await act(async () => {
      await delay(300);
    });

    expect(result.current.orgs).toHaveLength(2);
  });

  it("ignores a role that resolves after the user switched org again", async () => {
    server.use(
      ...authHandlers(),
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(envelope([fixtureOrg, secondOrg])),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/members`, async ({ params }) => {
        if (params.orgId === fixtureOrg.id) {
          await delay(150);
          return HttpResponse.json(envelope([makeMembership(user, "owner")]));
        }
        return HttpResponse.json(envelope([makeMembership(user, "member")]));
      }),
    );

    const { result } = renderOrg();
    await waitFor(() => {
      expect(result.current.orgs).toHaveLength(2);
    });

    act(() => {
      result.current.switchOrg(fixtureOrg.id);
    });
    act(() => {
      result.current.switchOrg(secondOrg.id);
    });

    await waitFor(() => {
      expect(result.current.role).toBe("member");
    });

    // The abandoned org's member list arrives now. Applying it would render
    // owner-only controls (ROLE_PERMISSIONS gates on this) for an org where
    // the caller is only a member.
    await act(async () => {
      await delay(300);
    });

    expect(result.current.org?.id).toBe(secondOrg.id);
    expect(result.current.role).toBe("member");
  });
});
