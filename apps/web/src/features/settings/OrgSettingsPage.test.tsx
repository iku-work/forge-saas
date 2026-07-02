import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { OrganizationPublic } from "@forge/api-client";

const hooks = vi.hoisted(() => ({ useOrg: vi.fn(), usePermission: vi.fn() }));
vi.mock("@forge/hooks", () => hooks);

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

    expect(screen.getByRole("heading", { name: "Organization" })).toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.getByText("Only owners and admins can change this.")).toBeInTheDocument();
  });
});
