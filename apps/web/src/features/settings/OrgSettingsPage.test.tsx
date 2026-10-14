import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { OrganizationPublic } from "@forge/api-client";

import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({ useOrg: vi.fn(), usePermission: vi.fn() }));
vi.mock("@forge/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@forge/hooks")>()),
  ...hooks,
}));

// The page sends you to the dashboard after leaving; no router is mounted
// here, so navigation is a spy.
const navigate = vi.hoisted(() => vi.fn());
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => navigate,
  useParams: (options?: {
    select?: (params: { locale: string }) => unknown;
  }) => (options?.select ? options.select({ locale: "en" }) : { locale: "en" }),
}));

const API = getApiBaseUrl();

const { OrgSettingsPage } = await import("./OrgSettingsPage");

const org: OrganizationPublic = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Acme Inc",
  slug: "acme-inc",
  logo_url: null,
  created_at: "2026-01-01T00:00:00Z",
};

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <OrgSettingsPage />
    </QueryClientProvider>,
  );
}

describe("OrgSettingsPage", () => {
  it("shows a loading skeleton while the org is still resolving", () => {
    hooks.useOrg.mockReturnValue({
      org: null,
      orgs: [],
      role: null,
      isLoading: true,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn(),
    });
    hooks.usePermission.mockReturnValue(false);

    renderPage();

    expect(
      screen.getByRole("heading", { name: "Organization" }),
    ).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Acme Inc")).not.toBeInTheDocument();
  });

  it("lets a manager edit and save the org name", () => {
    hooks.useOrg.mockReturnValue({
      org,
      orgs: [org],
      role: "owner",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn(),
    });
    hooks.usePermission.mockReturnValue(true);

    renderPage();

    expect(screen.getByDisplayValue("Acme Inc")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("disables editing for a member without SETTINGS_MANAGE", () => {
    hooks.useOrg.mockReturnValue({
      org,
      orgs: [org],
      role: "member",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn(),
    });
    hooks.usePermission.mockReturnValue(false);

    renderPage();

    expect(screen.getByDisplayValue("Acme Inc")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Only owners and admins can change this."),
    ).toBeInTheDocument();
  });

  it("leaves the organization after confirming, then goes to the dashboard", async () => {
    const refreshOrgs = vi.fn().mockResolvedValue(undefined);
    hooks.useOrg.mockReturnValue({
      org,
      orgs: [org],
      role: "member",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs,
    });
    hooks.usePermission.mockReturnValue(false);
    let left = false;
    server.use(
      http.post(`${API}/api/v1/orgs/:orgId/leave`, () => {
        left = true;
        return HttpResponse.json({
          data: null,
          error: null,
          meta: { request_id: "r1" },
        });
      }),
    );
    renderPage();

    await userEvent.click(screen.getByTestId("org-leave"));
    expect(left).toBe(false);
    await userEvent.click(
      await screen.findByRole("button", { name: "Leave organization" }),
    );

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({
        to: "/$locale",
        params: { locale: "en" },
      });
    });
    expect(left).toBe(true);
    expect(refreshOrgs).toHaveBeenCalled();
  });

  it("removes an uploaded logo by clearing logo_file_id", async () => {
    hooks.useOrg.mockReturnValue({
      org: { ...org, has_uploaded_logo: true },
      orgs: [org],
      role: "owner",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn().mockResolvedValue(undefined),
    });
    hooks.usePermission.mockReturnValue(true);
    let sent: unknown = null;
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/logo`, () =>
        HttpResponse.json({
          data: {
            url: "https://storage.example.test/logo.png",
            expires_at: "2026-12-31T00:00:00Z",
          },
          error: null,
          meta: { request_id: "r1" },
        }),
      ),
      http.patch(`${API}/api/v1/orgs/:orgId`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({
          data: { ...org, has_uploaded_logo: false },
          error: null,
          meta: { request_id: "r1" },
        });
      }),
    );
    renderPage();

    await userEvent.click(await screen.findByTestId("org-logo-remove"));

    await waitFor(() => {
      expect(sent).toEqual({ logo_file_id: null });
    });
  });
});
