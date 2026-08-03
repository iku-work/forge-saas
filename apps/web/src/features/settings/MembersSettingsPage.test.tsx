import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import {
  envelope,
  errorEnvelope,
  fixtureOrg,
  makeInvitation,
  makeMembership,
  makeUser,
} from "../../test/appShellFixtures";
import { server } from "../../test/server";
import { installVirtualLayout } from "../../test/virtualLayout";

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
  // T-241: the member list is virtualized, and a virtualizer that measures
  // a zero-height viewport (jsdom's answer for every element) renders zero
  // rows -- so even a one-member assertion needs a believable layout. The
  // numbers only have to be non-zero here; the DOM-node budget and the
  // scroll behaviour are asserted in MembersSettingsPageVirtual.test.tsx,
  // which is where they have to match the page's real constants.
  let restoreLayout: () => void;

  beforeEach(() => {
    restoreLayout = installVirtualLayout({
      rowHeight: 52,
      viewportHeight: 420,
    });
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

  afterEach(() => {
    restoreLayout();
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

  it("shows the pending-invitations card to an owner", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
        HttpResponse.json(
          envelope([makeInvitation(1, { email: "pending@example.com" })]),
        ),
      ),
    );

    renderPage();

    expect(await screen.findByText("pending@example.com")).toBeInTheDocument();
  });

  it("hides the pending-invitations card from a plain member", async () => {
    // Not cosmetic: GET /orgs/{id}/invitations is gated on MEMBERS_MANAGE
    // (it lists invitee email addresses), so rendering the card here would
    // put a 403 error box on the page.
    hooks.usePermission.mockReturnValue(false);
    const requestedPaths: string[] = [];
    server.events.on("request:start", ({ request }) => {
      requestedPaths.push(new URL(request.url).pathname);
    });
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "member")])),
      ),
    );

    renderPage();

    expect(await screen.findByText("You")).toBeInTheDocument();
    expect(screen.queryByText("Pending invitations")).not.toBeInTheDocument();
    expect(
      requestedPaths.some((path) => path.endsWith("/invitations")),
    ).toBe(false);
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
