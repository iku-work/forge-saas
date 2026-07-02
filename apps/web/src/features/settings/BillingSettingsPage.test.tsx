import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { PlanPublic, SubscriptionPublic } from "@forge/api-client";

import { envelope, fixtureOrg } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({ useOrg: vi.fn(), usePermission: vi.fn() }));
vi.mock("@forge/hooks", () => hooks);

const { BillingSettingsPage } = await import("./BillingSettingsPage");

const API = getApiBaseUrl();

const freePlan: PlanPublic = {
  id: "55555555-5555-5555-5555-555555555555",
  name: "Free",
  slug: "free",
  price_monthly: 0,
  price_yearly: 0,
  features: {},
};

const proPlan: PlanPublic = {
  id: "66666666-6666-6666-6666-666666666666",
  name: "Pro",
  slug: "pro",
  price_monthly: 2900,
  price_yearly: 29000,
  features: { seats: 10 },
};

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <BillingSettingsPage />
    </QueryClientProvider>,
  );
}

describe("BillingSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.useOrg.mockReturnValue({
      org: fixtureOrg,
      orgs: [fixtureOrg],
      role: "owner",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn(),
    });
  });

  it("shows a permission-denied message for a member without BILLING_READ", () => {
    hooks.usePermission.mockImplementation(
      (permission: string) => permission === "members:read",
    );

    renderPage();

    expect(
      screen.getByText(
        "You don't have permission to view billing for this organization.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the Free plan badge and lets an owner upgrade when there's no subscription", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan, proPlan])),
      ),
    );

    renderPage();

    expect(await screen.findByText("$29.00/mo")).toBeInTheDocument();
    // Neither catalog plan matches a (nonexistent) subscription's plan_id,
    // so both rows render an "Upgrade" action rather than "Current plan".
    expect(
      await screen.findAllByRole("button", { name: "Upgrade" }),
    ).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "Manage billing" }),
    ).not.toBeInTheDocument();
  });

  it("shows the current plan and lets an owner manage billing", async () => {
    hooks.usePermission.mockReturnValue(true);
    const subscription: SubscriptionPublic = {
      id: "77777777-7777-7777-7777-777777777777",
      plan_id: proPlan.id,
      status: "active",
      current_period_start: "2026-01-01T00:00:00Z",
      current_period_end: "2026-02-01T00:00:00Z",
      cancel_at_period_end: false,
      trial_end: null,
    };
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(subscription)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan, proPlan])),
      ),
    );

    renderPage();

    expect(await screen.findByText("Status: active")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Manage billing" }),
    ).toBeInTheDocument();
    // The Pro row is the current plan (disabled, labeled distinctly);
    // only the Free row offers an upgrade.
    expect(screen.getByRole("button", { name: "Current plan" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Upgrade" })).toBeInTheDocument();
  });
});
