import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type {
  InvoiceDto,
  PlanPublic,
  SubscriptionPublic,
  UsageSummary,
} from "@forge/api-client";
import { t } from "@forge/i18n";

import { envelope, errorEnvelope, fixtureOrg } from "../../test/appShellFixtures";
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
  currency: "usd",
  features: {},
};

const proPlan: PlanPublic = {
  id: "66666666-6666-6666-6666-666666666666",
  name: "Pro",
  slug: "pro",
  price_monthly: 2900,
  price_yearly: 29000,
  // Deliberately not USD: this page hardcoded "$" until 2026-09-10, so a
  // fixture in dollars cannot tell a page that reads the plan's currency
  // from one that assumes its own.
  currency: "eur",
  features: { seats: 10 },
};

function makeInvoice(id: string): InvoiceDto {
  return {
    id,
    number: `INV-${id}`,
    amount_paid: 2900,
    currency: "usd",
    status: "paid",
    hosted_invoice_url: `https://invoice.stripe.example/${id}`,
    invoice_pdf: `https://invoice.stripe.example/${id}.pdf`,
    period_start: "2026-01-01T00:00:00Z",
    period_end: "2026-02-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function renderPage() {
  // retry: false -- see FilesPage.test.tsx's own renderPage() for why: an
  // error-response test would otherwise wait through React Query's default
  // retry/backoff before settling, well past a single test's timeout.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
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

    // Euro, because proPlan is priced in euro. The invoice assertions below
    // stay in dollars -- an InvoiceDTO carries its own currency, and that one
    // was always threaded through. The plan's was not.
    expect(await screen.findByText("€29.00/mo")).toBeInTheDocument();
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

  it("shows an empty-state message when the org has no invoices", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan])),
      ),
      http.get(`${API}/api/v1/billing/invoices`, () =>
        HttpResponse.json(envelope([])),
      ),
    );

    renderPage();

    expect(await screen.findByText("No invoices yet.")).toBeInTheDocument();
  });

  it("lists invoices with formatted amount, status, and a PDF download link", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan])),
      ),
      http.get(`${API}/api/v1/billing/invoices`, () =>
        HttpResponse.json(envelope([makeInvoice("in_1")])),
      ),
    );

    renderPage();

    expect(await screen.findByText("$29.00")).toBeInTheDocument();
    expect(screen.getByText("paid")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Download PDF" });
    expect(link).toHaveAttribute(
      "href",
      "https://invoice.stripe.example/in_1.pdf",
    );
  });

  it("shows a Load more button that fetches the next cursor page", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan])),
      ),
      http.get(`${API}/api/v1/billing/invoices`, ({ request }) => {
        const after = new URL(request.url).searchParams.get("after");
        if (after === null) {
          return HttpResponse.json({
            ...envelope([makeInvoice("in_1")]),
            meta: { request_id: "req-1", next_cursor: "in_1" },
          });
        }
        return HttpResponse.json(envelope([makeInvoice("in_2")]));
      }),
    );

    renderPage();

    const loadMore = await screen.findByRole("button", { name: "Load more" });
    await userEvent.click(loadMore);

    await waitFor(() => {
      expect(screen.getAllByText("$29.00")).toHaveLength(2);
    });
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });

  it("shows no usage section when the org has no metered usage", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan])),
      ),
    );

    renderPage();

    await screen.findByTestId("billing-upgrade-free");
    expect(screen.queryByTestId("billing-usage-table")).not.toBeInTheDocument();
  });

  it("shows usage this period with a formatted sub-cent estimated cost", async () => {
    hooks.usePermission.mockReturnValue(true);
    const usage: UsageSummary[] = [
      {
        metric: "api_calls",
        quantity: 42,
        unit_price_micros: 100, // $0.0001/call
        estimated_cost_micros: 4_200,
      },
      {
        metric: "exports",
        quantity: 3,
        unit_price_micros: null,
        estimated_cost_micros: null,
      },
    ];
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan])),
      ),
      http.get(`${API}/api/v1/billing/usage`, () =>
        HttpResponse.json(envelope(usage)),
      ),
    );

    renderPage();

    expect(await screen.findByText("api_calls")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("$0.0042")).toBeInTheDocument();
    expect(screen.getByText("exports")).toBeInTheDocument();
    expect(screen.getByText("Not priced")).toBeInTheDocument();
  });

  it("shows an error message when usage fails to load", async () => {
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan])),
      ),
      http.get(`${API}/api/v1/billing/usage`, () =>
        HttpResponse.json(errorEnvelope("network down"), { status: 500 }),
      ),
    );

    renderPage();

    expect(
      await screen.findByText(t("errors.internal_error", {}, "en")),
    ).toBeInTheDocument();
  });
});

describe("BillingSettingsPage duplicate-subscription guard", () => {
  beforeEach(() => {
    hooks.useOrg.mockReturnValue({ org: fixtureOrg, isLoading: false });
    hooks.usePermission.mockReturnValue(true);
  });

  it("follows the portal URL when checkout is refused for an existing subscription", async () => {
    // The server answers 409 with the portal URL in `error.details` rather
    // than opening a second checkout. Pressing a plan button must land the
    // customer somewhere useful, not on an error banner.
    const assigned: string[] = [];
    const location = window.location;
    // A hand-built stub rather than `{...location}`: Location is a class
    // instance, so spreading it drops the prototype (and eslint's
    // no-misused-spread rightly objects). The page only ever writes `href`.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        assign: (url: string) => assigned.push(url),
        replace: (url: string) => assigned.push(url),
        set href(url: string) {
          assigned.push(url);
        },
        get href() {
          return "https://app.test/en/settings/billing";
        },
      },
    });

    server.use(
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan, proPlan])),
      ),
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(envelope(null)),
      ),
      http.post(`${API}/api/v1/billing/checkout-session`, () =>
        HttpResponse.json(
          {
            data: null,
            error: {
              code: "conflict",
              message_key: "errors.subscription_already_active",
              message: "already subscribed",
              details: { portal_url: "https://billing.stripe.test/portal/abc" },
            },
            meta: { request_id: "r1" },
          },
          { status: 409 },
        ),
      ),
    );

    renderPage();

    // findAllBy + [0] is `HTMLElement | undefined` under
    // noUncheckedIndexedAccess; assert on the list first so a page that
    // renders no Upgrade button fails here rather than on a cryptic click.
    const [upgrade] = await screen.findAllByRole("button", {
      name: "Upgrade",
    });
    // Narrowed rather than asserted: noUncheckedIndexedAccess makes this
    // `HTMLElement | undefined`, and a page that renders no Upgrade button
    // should fail here with a sentence rather than on a cryptic click.
    if (!upgrade) throw new Error("no Upgrade button rendered");
    await userEvent.click(upgrade);

    await waitFor(() => {
      expect(assigned).toEqual(["https://billing.stripe.test/portal/abc"]);
    });

    Object.defineProperty(window, "location", {
      configurable: true,
      value: location,
    });
  });
});

describe("BillingSettingsPage after a subscription is cancelled", () => {
  beforeEach(() => {
    hooks.useOrg.mockReturnValue({ org: fixtureOrg, isLoading: false });
    hooks.usePermission.mockReturnValue(true);
  });

  it("shows Free and re-enables the plan button once the subscription is canceled", async () => {
    // A cancelled row keeps pointing at the plan it used to grant. Deriving
    // the badge from plan_id alone claimed "Pro" beside a status line
    // reading "canceled", and disabled Pro's button as "Current plan" --
    // stranding a customer who wanted to resubscribe on the one page built
    // for exactly that.
    server.use(
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan, proPlan])),
      ),
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(
          envelope({
            id: "77777777-7777-7777-7777-777777777777",
            plan_id: proPlan.id,
            status: "canceled",
            current_period_start: "2026-09-12T08:13:07Z",
            current_period_end: "2026-10-12T08:13:07Z",
            cancel_at_period_end: false,
            trial_end: null,
          } satisfies SubscriptionPublic),
        ),
      ),
    );

    renderPage();

    expect(await screen.findByTestId("billing-current-plan")).toHaveTextContent(
      "Free",
    );
    expect(screen.getByTestId("billing-upgrade-pro")).toBeEnabled();
    expect(screen.getByTestId("billing-upgrade-pro")).toHaveTextContent(
      "Upgrade",
    );
  });

  it("still shows the plan while a payment is being chased", async () => {
    // past_due is live: the plan is still theirs during the grace period
    // (_is_entitled), so the page must not tell them they are on Free.
    server.use(
      http.get(`${API}/api/v1/billing/plans`, () =>
        HttpResponse.json(envelope([freePlan, proPlan])),
      ),
      http.get(`${API}/api/v1/billing/subscription`, () =>
        HttpResponse.json(
          envelope({
            id: "88888888-8888-8888-8888-888888888888",
            plan_id: proPlan.id,
            status: "past_due",
            current_period_start: "2026-09-12T08:13:07Z",
            current_period_end: "2026-10-12T08:13:07Z",
            cancel_at_period_end: false,
            trial_end: null,
          } satisfies SubscriptionPublic),
        ),
      ),
    );

    renderPage();

    expect(await screen.findByTestId("billing-current-plan")).toHaveTextContent(
      "Pro",
    );
  });
});
