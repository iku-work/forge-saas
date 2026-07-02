import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import {
  envelope,
  errorEnvelope,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "../../test/appShellFixtures";
import { server } from "../../test/server";

// Mocking @forge/hooks directly (rather than the SDK calls underneath, as
// the AppLayout integration tests do) sidesteps useOrg/useAuth's
// module-scoped bootstrap caching entirely -- each test below can set
// whatever role/org/user it wants without fighting "already bootstrapped
// for this user id" from a previous test in the file.
const hooks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useOrg: vi.fn(),
  usePermission: vi.fn(),
}));
vi.mock("@forge/hooks", () => hooks);

const { MembersSettingsPage } = await import("./MembersSettingsPage");

const API = getApiBaseUrl();
const user = makeUser(4);

function renderPage() {
  // retry: false -- react-query's default 3-retries-with-backoff would
  // otherwise keep the error-state test's query pending past findBy's
  // default timeout before it ever settles into isError.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MembersSettingsPage />
    </QueryClientProvider>,
  );
}

describe("MembersSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.useAuth.mockReturnValue({
      user,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      register: vi.fn(),
    });
    hooks.useOrg.mockReturnValue({
      org: fixtureOrg,
      orgs: [fixtureOrg],
      role: "owner",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn(),
    });
  });

  it("shows the Invite button for an owner (has MEMBERS_MANAGE)", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
    );

    renderPage();

    expect(
      await screen.findByRole("button", { name: "Invite" }),
    ).toBeInTheDocument();
  });

  it("hides the Invite button for a plain member (no MEMBERS_MANAGE)", async () => {
    hooks.usePermission.mockReturnValue(false);
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "member")])),
      ),
    );

    renderPage();

    expect(await screen.findByText("You")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Invite" }),
    ).not.toBeInTheDocument();
  });

  it("shows an empty state when the org has no members", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([])),
      ),
    );

    renderPage();

    expect(await screen.findByText("No members yet.")).toBeInTheDocument();
  });

  it("shows an error state when the members request fails", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(errorEnvelope("network down"), { status: 500 }),
      ),
    );

    renderPage();

    // getErrorMessage() re-translates by envelope.error.code -- the
    // backend's own message text ("network down") is never shown directly
    // (T-069); errorEnvelope()'s default code is "internal_error".
    expect(
      await screen.findByText(t("errors.internal_error", {}, "en")),
    ).toBeInTheDocument();
  });
});
