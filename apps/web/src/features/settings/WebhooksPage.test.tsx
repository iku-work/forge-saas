import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type {
  WebhookDeliveryPublic,
  WebhookEndpointPublic,
} from "@forge/api-client";

import { envelope, fixtureOrg, makeUser } from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import { server } from "../../test/server";
import { installVirtualLayout } from "../../test/virtualLayout";

const hooks = vi.hoisted(() => ({
  useOrg: vi.fn(),
  usePermission: vi.fn(),
}));
vi.mock("@forge/hooks", () => hooks);

const { WebhooksPage } = await import("./WebhooksPage");

const API = getApiBaseUrl();
const user = makeUser(11);

const endpoint: WebhookEndpointPublic = {
  id: "bbbbbbbb-0000-0000-0000-000000000001",
  url: "https://consumer.example.com/hook",
  description: "Order sync",
  event_types: ["member.invited"],
  enabled: true,
  consecutive_failures: 0,
  disabled_at: null,
  created_by: user.id,
  created_at: "2026-02-01T00:00:00Z",
  updated_at: "2026-02-01T00:00:00Z",
  secret_previous_expires_at: null,
};

const delivery: WebhookDeliveryPublic = {
  id: "cccccccc-0000-0000-0000-000000000001",
  endpoint_id: endpoint.id,
  event_type: "member.invited",
  status: "failed",
  attempt_count: 2,
  response_code: 500,
  response_body: "boom",
  error: "HTTP 500",
  next_attempt_at: "2026-02-01T00:05:00Z",
  delivered_at: null,
  delivery_id: "dddddddd-0000-0000-0000-000000000001",
  replay_of_id: null,
  created_at: "2026-02-01T00:00:00Z",
  payload: null,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WebhooksPage />
    </QueryClientProvider>,
  );
}

describe("WebhooksPage", () => {
  // The delivery log is the one virtualized list on this page (T-241/T-322),
  // and a virtualizer measuring jsdom's zero-height viewport renders zero
  // rows -- so even a one-row assertion needs a believable layout. The
  // numbers match the page's own constants.
  let restoreLayout: () => void;

  beforeEach(() => {
    restoreLayout = installVirtualLayout({
      rowHeight: 56,
      viewportHeight: 420,
    });
    vi.clearAllMocks();
    hooks.useOrg.mockReturnValue({
      org: fixtureOrg,
      orgs: [fixtureOrg],
      isLoading: false,
      switchOrg: vi.fn(),
    });
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/webhooks/endpoints`, () =>
        HttpResponse.json(envelope([endpoint])),
      ),
      http.get(`${API}/api/v1/webhooks/event-types`, () =>
        HttpResponse.json(
          envelope([
            {
              name: "member.invited",
              description: "An invitation was sent to join the organization.",
            },
            {
              name: "org.updated",
              description: "The organization's name or slug changed.",
            },
          ]),
        ),
      ),
      http.get(
        `${API}/api/v1/webhooks/endpoints/:endpointId/deliveries`,
        () => HttpResponse.json(envelope([delivery])),
      ),
    );
  });

  afterEach(() => {
    restoreLayout();
    vi.restoreAllMocks();
  });

  it("lists an endpoint with its URL and the events it is subscribed to", async () => {
    renderPage();

    expect(
      await screen.findByText("https://consumer.example.com/hook"),
    ).toBeInTheDocument();
    expect(screen.getByText("member.invited")).toBeInTheDocument();
  });

  it("renders the event picker from the server's registry, not a hardcoded list", async () => {
    // T-320's registry is the single source: a product that registers a new
    // event gets a checkbox for it without anyone touching this page.
    const person = userEvent.setup();
    renderPage();

    await person.click(await screen.findByTestId("webhooks-create-button"));

    expect(screen.getByTestId("webhook-event-member.invited")).toBeInTheDocument();
    expect(screen.getByTestId("webhook-event-org.updated")).toBeInTheDocument();
  });

  it("shows the signing secret once, and forgets it on close", async () => {
    const secret = "whsec_abc123";
    server.use(
      http.post(`${API}/api/v1/webhooks/endpoints`, () =>
        HttpResponse.json(envelope({ endpoint, secret })),
      ),
    );
    const person = userEvent.setup();
    renderPage();

    await person.click(await screen.findByTestId("webhooks-create-button"));
    await person.type(
      screen.getByTestId("webhook-url-input"),
      "https://consumer.example.com/new",
    );
    await person.click(screen.getByTestId("webhook-event-member.invited"));
    await person.click(screen.getByTestId("webhook-submit"));

    expect(await screen.findByTestId("webhook-secret-value")).toHaveTextContent(
      secret,
    );
    await person.click(screen.getByTestId("webhook-secret-done"));

    await waitFor(() => {
      expect(screen.queryByTestId("webhook-secret-value")).not.toBeInTheDocument();
    });
  });

  it("says when the previous secret stops working after a rotation", async () => {
    // A rotation window a consumer cannot see is one they have to guess the
    // deadline for.
    server.use(
      http.post(
        `${API}/api/v1/webhooks/endpoints/:endpointId/rotate-secret`,
        () =>
          HttpResponse.json(
            envelope({
              endpoint,
              secret: "whsec_new",
              previous_secret_expires_at: "2026-02-02T00:00:00Z",
            }),
          ),
      ),
    );
    const person = userEvent.setup();
    renderPage();

    await person.click(await screen.findByTestId(`webhook-rotate-${endpoint.id}`));

    expect(await screen.findByTestId("webhook-rotated-value")).toHaveTextContent(
      "whsec_new",
    );
    expect(screen.getByTestId("webhook-rotated-warning")).toHaveTextContent(
      "Feb 2, 2026",
    );
  });

  it("replays a delivery without touching the original row", async () => {
    let replayedId: string | null = null;
    server.use(
      http.post(
        `${API}/api/v1/webhooks/deliveries/:deliveryId/replay`,
        ({ params }) => {
          replayedId = String(params.deliveryId);
          return HttpResponse.json(
            envelope({
              ...delivery,
              id: "cccccccc-0000-0000-0000-000000000002",
              status: "pending",
              attempt_count: 0,
              delivery_id: "dddddddd-0000-0000-0000-000000000002",
              replay_of_id: delivery.id,
            }),
          );
        },
      ),
    );
    const person = userEvent.setup();
    renderPage();

    await person.click(
      await screen.findByTestId(`webhook-deliveries-${endpoint.id}`),
    );
    await person.click(await screen.findByTestId(`delivery-replay-${delivery.id}`));

    expect(await screen.findByTestId("delivery-replayed")).toBeInTheDocument();
    expect(replayedId).toBe(delivery.id);
  });

  it("requires the confirmation word before deleting an endpoint", async () => {
    let deleted = false;
    server.use(
      http.delete(`${API}/api/v1/webhooks/endpoints/:endpointId`, () => {
        deleted = true;
        return HttpResponse.json(envelope(null));
      }),
    );
    const person = userEvent.setup();
    renderPage();

    await person.click(await screen.findByTestId(`webhook-delete-${endpoint.id}`));
    const submit = screen.getByTestId("webhook-delete-confirm-submit");
    expect(submit).toBeDisabled();

    await person.type(
      screen.getByTestId("webhook-delete-confirm-input"),
      "DELETE",
    );
    await person.click(submit);

    await waitFor(() => {
      expect(deleted).toBe(true);
    });
  });

  it("says why an endpoint was switched off rather than only that it is", async () => {
    server.use(
      http.get(`${API}/api/v1/webhooks/endpoints`, () =>
        HttpResponse.json(
          envelope([
            {
              ...endpoint,
              enabled: false,
              consecutive_failures: 5,
              disabled_at: "2026-02-02T00:00:00Z",
            },
          ]),
        ),
      ),
    );
    renderPage();

    expect(
      await screen.findByText("Switched off after 5 failures"),
    ).toBeInTheDocument();
  });

  it("shows a member nothing but why", async () => {
    hooks.usePermission.mockReturnValue(false);
    renderPage();

    expect(
      await screen.findByTestId("webhooks-permission-denied"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("webhooks-create-button")).not.toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = renderPage();

    await screen.findByText("https://consumer.example.com/hook");
    await expectNoA11yViolations(container);
  });
});
