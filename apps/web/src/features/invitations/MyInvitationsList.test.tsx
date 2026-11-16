import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { MyInvitation } from "@forge/api-client";

import { envelope, fixtureUser } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const navigate = vi.fn();
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => navigate,
  useParams: (options?: {
    select?: (params: { locale: string }) => unknown;
  }) => (options?.select ? options.select({ locale: "en" }) : { locale: "en" }),
}));

const org = vi.hoisted(() => ({
  refreshOrgs: vi.fn(() => Promise.resolve()),
  switchOrg: vi.fn(),
}));
const hooks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useOrg: () => org,
}));
vi.mock("@forge/hooks", () => hooks);

const { MyInvitationsList } = await import("./MyInvitationsList");

const API = getApiBaseUrl();

const ACME: MyInvitation = {
  id: "77777777-7777-7777-7777-000000000001",
  org_id: "11111111-1111-1111-1111-111111111111",
  org_name: "Acme",
  role: "admin",
  expires_at: "2026-12-01T00:00:00Z",
};
const GLOBEX: MyInvitation = {
  ...ACME,
  id: "77777777-7777-7777-7777-000000000002",
  org_id: "22222222-2222-2222-2222-222222222222",
  org_name: "Globex",
  role: "member",
};

function renderList(quietWhenEmpty = false) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MyInvitationsList quietWhenEmpty={quietWhenEmpty} />
    </QueryClientProvider>,
  );
}

function serve(invitations: MyInvitation[]) {
  server.use(
    http.get(`${API}/api/v1/me/invitations`, () =>
      HttpResponse.json(envelope(invitations)),
    ),
  );
}

describe("MyInvitationsList", () => {
  beforeEach(() => {
    navigate.mockReset();
    org.refreshOrgs.mockClear();
    org.switchOrg.mockClear();
    hooks.useAuth.mockReturnValue({
      user: { ...fixtureUser, email_verified_at: "2026-01-01T00:00:00Z" },
    });
  });

  it("lists each invitation with its org and role", async () => {
    serve([ACME, GLOBEX]);
    renderList();

    const rows = await screen.findAllByTestId("my-invitation-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("Acme")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Join as Admin")).toBeInTheDocument();
  });

  it("joins, opens the org and leaves the page on accept", async () => {
    serve([ACME]);
    let accepted = "";
    server.use(
      http.post(`${API}/api/v1/me/invitations/:id/accept`, ({ params }) => {
        accepted = String(params.id);
        return HttpResponse.json(envelope({}));
      }),
    );
    renderList();

    await userEvent.click(
      await screen.findByRole("button", { name: "Accept" }),
    );

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({
        to: "/$locale",
        params: { locale: "en" },
      });
    });
    expect(accepted).toBe(ACME.id);
    expect(org.refreshOrgs).toHaveBeenCalled();
    expect(org.switchOrg).toHaveBeenCalledWith(ACME.org_id);
  });

  it("declines without leaving the page", async () => {
    serve([ACME]);
    let declined = "";
    server.use(
      http.post(`${API}/api/v1/me/invitations/:id/decline`, ({ params }) => {
        declined = String(params.id);
        serve([]);
        return HttpResponse.json(envelope(null));
      }),
    );
    renderList();

    await userEvent.click(
      await screen.findByRole("button", { name: "Decline" }),
    );

    expect(await screen.findByTestId("no-my-invitations")).toHaveTextContent(
      "You have no open invitations.",
    );
    expect(declined).toBe(ACME.id);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("asks an unverified address to verify first", async () => {
    hooks.useAuth.mockReturnValue({
      user: { ...fixtureUser, email_verified_at: null },
    });
    serve([]);
    renderList();

    expect(await screen.findByTestId("no-my-invitations")).toHaveTextContent(
      "Verify your email address to see invitations here.",
    );
  });

  it("shows nothing on the first-organization screen when there is nothing", async () => {
    serve([]);
    const { container } = renderList(true);

    await waitFor(() => {
      expect(container).toBeEmptyDOMElement();
    });
  });
});
