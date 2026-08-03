import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import {
  envelope,
  errorEnvelope,
  fixtureOrg,
  makeInvitation,
} from "../../test/appShellFixtures";
import { server } from "../../test/server";
import { installVirtualLayout } from "../../test/virtualLayout";

import { PendingInvitationsCard } from "./PendingInvitationsCard";

const API = getApiBaseUrl();

function renderCard() {
  // retry: false -- react-query's default 3-retries-with-backoff would
  // otherwise keep the error-state test pending past findBy's timeout.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PendingInvitationsCard orgId={fixtureOrg.id} />
    </QueryClientProvider>,
  );
}

describe("PendingInvitationsCard", () => {
  // T-241: the list virtualizes, and a virtualizer measuring jsdom's
  // zero-height everything renders zero rows -- so even a one-row assertion
  // needs a believable layout.
  let restoreLayout: () => void;

  beforeEach(() => {
    restoreLayout = installVirtualLayout({
      rowHeight: 52,
      viewportHeight: 420,
    });
  });

  afterEach(() => {
    restoreLayout();
  });

  it("lists a pending invitation by email and role", async () => {
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
        HttpResponse.json(
          envelope([makeInvitation(1, { email: "new@example.com" })]),
        ),
      ),
    );

    renderCard();

    const row = await screen.findByTestId("invitation-row");
    expect(within(row).getByText("new@example.com")).toBeInTheDocument();
    expect(within(row).getByText("Member")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is pending", async () => {
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
        HttpResponse.json(envelope([])),
      ),
    );

    renderCard();

    expect(
      await screen.findByText("No pending invitations."),
    ).toBeInTheDocument();
  });

  it("marks an expired invitation as expired rather than hiding it", async () => {
    // The backend filters on accepted_at only, deliberately -- an expired
    // invite is the row an admin needs in order to re-send it, so the UI
    // has to label it rather than drop it.
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
        HttpResponse.json(
          envelope([
            makeInvitation(2, {
              email: "lapsed@example.com",
              expires_at: "2020-01-01T00:00:00Z",
            }),
          ]),
        ),
      ),
    );

    renderCard();

    const row = await screen.findByTestId("invitation-row");
    expect(within(row).getByText("lapsed@example.com")).toBeInTheDocument();
    expect(within(row).getByText("Expired")).toBeInTheDocument();
  });

  it("shows the expiry date for an invitation that is still live", async () => {
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
        HttpResponse.json(
          envelope([makeInvitation(3, { expires_at: "2099-06-15T00:00:00Z" })]),
        ),
      ),
    );

    renderCard();

    const row = await screen.findByTestId("invitation-row");
    expect(within(row).queryByText("Expired")).not.toBeInTheDocument();
    expect(within(row).getByText(/Expires/)).toBeInTheDocument();
  });

  it("shows an error state when the request fails", async () => {
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
        HttpResponse.json(errorEnvelope("boom"), { status: 500 }),
      ),
    );

    renderCard();

    // getErrorMessage() re-translates by envelope.error.code -- the
    // backend's own message text is never shown directly (T-069).
    expect(
      await screen.findByText(t("errors.internal_error", {}, "en")),
    ).toBeInTheDocument();
  });

  it("renders every invitation on a page", async () => {
    const invitations = Array.from({ length: 8 }, (_, index) =>
      makeInvitation(index + 10),
    );
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
        HttpResponse.json(envelope(invitations)),
      ),
    );

    renderCard();

    // 420px viewport / 52px rows + overscan comfortably covers 8.
    await screen.findByText("invitee-10@example.com");
    expect(screen.getAllByTestId("invitation-row")).toHaveLength(
      invitations.length,
    );
  });
});
