import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  // The row menu's dropdown reads the text direction to place itself.
  useDirection: vi.fn(() => "ltr"),
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
      rowHeight: 72,
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

  describe("who each member is (visual overhaul follow-up)", () => {
    const other = makeUser(6, {
      full_name: "Sam Rivera",
      email: "sam@example.com",
    });
    const members = [
      makeMembership(user, "owner"),
      {
        ...makeMembership(other, "member"),
        id: "44444444-4444-4444-4444-444444444444",
      },
    ];

    it("shows another member's name and email, and the count", async () => {
      hooks.usePermission.mockReturnValue(true);
      server.use(
        http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
          HttpResponse.json({
            ...envelope(members),
            meta: { request_id: "req-1", total: 2 },
          }),
        ),
      );
      renderPage();

      expect(await screen.findByText("Sam Rivera")).toBeInTheDocument();
      expect(screen.getByText("sam@example.com")).toBeInTheDocument();
      expect(screen.getByTestId("members-count")).toHaveTextContent(
        "2 members",
      );
    });

    it("gives a plain member no email search, and names a nameless member", async () => {
      hooks.usePermission.mockReturnValue(false);
      server.use(
        http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
          HttpResponse.json(
            envelope([
              ...members,
              {
                ...makeMembership(makeUser(7), "member"),
                id: "55555555-5555-5555-5555-555555555555",
                // What the API sends a plain member: no name set, email withheld.
                user: {
                  full_name: null,
                  email: null,
                  avatar_url: null,
                  has_uploaded_avatar: false,
                },
              },
            ]),
          ),
        ),
      );
      renderPage();

      expect(await screen.findByText("Unnamed member")).toBeInTheDocument();
      expect(screen.queryByTestId("members-search")).not.toBeInTheDocument();
    });

    it("searches by email on the server, not by filtering the loaded page", async () => {
      hooks.usePermission.mockReturnValue(true);
      const queries: (string | null)[] = [];
      server.use(
        http.get(`${API}/api/v1/orgs/:orgId/members`, ({ request }) => {
          const q = new URL(request.url).searchParams.get("q");
          queries.push(q);
          const rows = q ? members.slice(1) : members;
          return HttpResponse.json({
            ...envelope(rows),
            meta: { request_id: "req-1", total: rows.length },
          });
        }),
      );
      renderPage();
      await screen.findByText("Sam Rivera");

      await userEvent.type(screen.getByTestId("members-search"), "sam");

      await waitFor(() => {
        expect(queries).toContain("sam");
      });
      expect(await screen.findByTestId("members-count")).toHaveTextContent(
        "1 member",
      );
    });
  });

  describe("row actions", () => {
    const other = makeUser(5, {
      full_name: "Sam Rivera",
      email: "sam@example.com",
    });

    beforeEach(() => {
      server.use(
        http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
          HttpResponse.json(
            envelope([
              makeMembership(user, "owner"),
              // Its own id: makeMembership's is fixed, and the list keys rows by it.
              {
                ...makeMembership(other, "member"),
                id: "44444444-4444-4444-4444-444444444444",
              },
            ]),
          ),
        ),
      );
    });

    it("puts change-role and a separated, red remove in the other member's menu", async () => {
      hooks.usePermission.mockReturnValue(true);
      renderPage();

      await userEvent.click(
        await screen.findByRole("button", {
          name: "Actions for Sam Rivera",
        }),
      );

      const menu = await screen.findByRole("menu");
      expect(
        within(menu).getByRole("menuitem", { name: "Change role" }),
      ).toBeInTheDocument();
      expect(within(menu).getByRole("separator")).toBeInTheDocument();
      expect(
        within(menu).getByRole("menuitem", {
          name: "Remove from organization",
        }),
      ).toHaveClass("text-destructive");
    });

    it("removes the member when Remove is chosen", async () => {
      hooks.usePermission.mockReturnValue(true);
      let removed: string | null = null;
      server.use(
        http.delete(
          `${API}/api/v1/orgs/:orgId/members/:membershipId`,
          ({ params }) => {
            removed = String(params.membershipId);
            return new HttpResponse(null, { status: 204 });
          },
        ),
      );
      renderPage();

      await userEvent.click(
        await screen.findByRole("button", {
          name: "Actions for Sam Rivera",
        }),
      );
      await userEvent.click(
        await screen.findByRole("menuitem", {
          name: "Remove from organization",
        }),
      );
      // F-009: named, and nothing sent until it is confirmed.
      const dialog = await screen.findByTestId("confirm-dialog");
      expect(dialog).toHaveTextContent("Remove Sam Rivera?");
      expect(removed).toBeNull();
      await userEvent.click(
        within(dialog).getByRole("button", {
          name: "Remove from organization",
        }),
      );

      await waitFor(() => {
        expect(removed).not.toBeNull();
      });
    });

    it("offers no actions on your own row, nor to a plain member", async () => {
      hooks.usePermission.mockReturnValue(true);
      const { unmount } = renderPage();
      await screen.findByRole("button", { name: "Actions for Sam Rivera" });
      expect(
        screen.queryByRole("button", { name: "Actions for Jane Doe" }),
      ).not.toBeInTheDocument();
      unmount();

      hooks.usePermission.mockReturnValue(false);
      renderPage();
      await screen.findByText("You");
      expect(
        screen.queryByRole("button", { name: /^Actions for/ }),
      ).not.toBeInTheDocument();
    });
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
    expect(requestedPaths.some((path) => path.endsWith("/invitations"))).toBe(
      false,
    );
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
