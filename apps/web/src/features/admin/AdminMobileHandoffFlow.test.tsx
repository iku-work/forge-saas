import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { AdminUserDetail } from "@forge/api-client";

import { AppRoot } from "../../AppRoot";
import { createAppRouter } from "../../router";
import { envelope, makeUser } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// Only the payload segment is ever read client-side (see @forge/api-client's
// getImpersonatorId) -- same helper as AdminImpersonateFlow.test.tsx.
function fakeJwt(payload: Record<string, unknown>): string {
  const segment = btoa(JSON.stringify(payload))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `header.${segment}.signature`;
}

const superadmin = makeUser(30, {
  email: "root@example.com",
  is_superadmin: true,
});
const target = makeUser(31, {
  email: "target@example.com",
  is_superadmin: false,
});
const impersonationToken = fakeJwt({
  sub: target.id,
  impersonator_id: superadmin.id,
});

const targetDetail: AdminUserDetail = {
  id: target.id,
  email: target.email,
  full_name: target.full_name,
  is_active: true,
  is_superadmin: false,
  created_at: "2026-01-01T00:00:00Z",
  memberships: [],
  sessions: [],
  audit_summary: { total_count: 0, recent_actions: [] },
};

const DEEP_LINK = `forge://impersonate?token=exchange-abc&user_email=${encodeURIComponent(target.email)}`;

function renderFullApp(initialPath: string) {
  const queryClient = new QueryClient();
  const router = createAppRouter(
    queryClient,
    createMemoryHistory({ initialEntries: [initialPath] }),
  );
  const result = render(
    <QueryClientProvider client={queryClient}>
      <AppRoot router={router} queryClient={queryClient} />
    </QueryClientProvider>,
  );
  return { ...result, router };
}

interface Handlers {
  onHandoff?: (authorization: string | null) => void;
  onEnd?: (authorization: string | null) => void;
}

function stubAdminApi({ onHandoff, onEnd }: Handlers = {}) {
  server.use(
    http.post(`${API}/api/v1/auth/refresh`, () =>
      HttpResponse.json(
        envelope({ access_token: fakeJwt({ sub: superadmin.id }) }),
      ),
    ),
    http.get(`${API}/api/v1/auth/me`, () =>
      HttpResponse.json(envelope(superadmin)),
    ),
    http.get(`${API}/api/v1/admin/users/${target.id}`, () =>
      HttpResponse.json(envelope(targetDetail)),
    ),
    http.post(`${API}/api/v1/admin/users/${target.id}/impersonate`, () =>
      HttpResponse.json(
        envelope({
          access_token: impersonationToken,
          expires_at: "2026-01-01T01:00:00Z",
          user: target,
        }),
      ),
    ),
    http.post(
      `${API}/api/v1/admin/impersonate/mobile-handoff`,
      ({ request }) => {
        onHandoff?.(request.headers.get("authorization"));
        return HttpResponse.json(
          envelope({
            deep_link: DEEP_LINK,
            expires_at: new Date(Date.now() + 120_000).toISOString(),
          }),
        );
      },
    ),
    http.post(`${API}/api/v1/admin/impersonate/end`, ({ request }) => {
      onEnd?.(request.headers.get("authorization"));
      return HttpResponse.json(envelope(null));
    }),
    // AdminLayout's nav preloads its <Link>s -- see AdminImpersonateFlow's
    // note on why these are needed even though this test never visits them.
    http.get(`${API}/api/v1/orgs`, () => HttpResponse.json(envelope([]))),
    http.get(`${API}/api/v1/admin/users`, () =>
      HttpResponse.json(envelope([])),
    ),
    http.get(`${API}/api/v1/admin/orgs`, () => HttpResponse.json(envelope([]))),
    http.get(`${API}/api/v1/admin/audit`, () =>
      HttpResponse.json(envelope([])),
    ),
    http.get(`${API}/api/v1/admin/dashboard`, () =>
      HttpResponse.json(
        envelope({
          org_count: 0,
          user_count: 0,
          mrr_cents: null,
          recent_signups: [],
          recent_payment_failures: [],
        }),
      ),
    ),
  );
}

async function submitHandoffForm() {
  await userEvent.click(
    await screen.findByRole("button", { name: "Hand off to my phone" }),
  );
  await screen.findByText(`Hand off ${target.email} to your phone`);
  await userEvent.type(
    screen.getByLabelText("Reason (min. 10 characters)"),
    "Reproducing an iOS upload crash",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Create handoff link" }),
  );
}

describe("Admin mobile handoff flow (T-261)", () => {
  it("creates a link from a fresh impersonation session and renders it as a QR code", async () => {
    const authorizations: (string | null)[] = [];
    stubAdminApi({ onHandoff: (auth) => authorizations.push(auth) });

    renderFullApp(`/admin/users/${target.id}`);
    await submitHandoffForm();

    expect(await screen.findByTestId("handoff-deep-link")).toHaveTextContent(
      DEEP_LINK,
    );
    // The QR encodes the same link, so an operator can scan it or copy it.
    expect(
      screen.getByTitle("Impersonation handoff deep link"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Link expires in \d+s/)).toBeInTheDocument();
    // The handoff call goes out as the *impersonation* session, not as the
    // superadmin's own token -- the backend requires an active impersonation
    // session, and the module-level token is still the superadmin's.
    expect(authorizations).toEqual([`Bearer ${impersonationToken}`]);
  }, 15000);

  it("leaves the admin signed in as themselves", async () => {
    stubAdminApi();

    renderFullApp(`/admin/users/${target.id}`);
    await submitHandoffForm();
    await screen.findByTestId("handoff-deep-link");

    // The whole point of handing off rather than impersonating: no banner,
    // because this browser never adopted the session it just created. A
    // superadmin left impersonating in a forgotten tab is exactly what this
    // flow avoids.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "End impersonation" }),
    ).not.toBeInTheDocument();
  }, 15000);

  it("reports a failed handoff instead of showing a dead link", async () => {
    stubAdminApi();
    server.use(
      http.post(`${API}/api/v1/admin/impersonate/mobile-handoff`, () =>
        HttpResponse.json(
          {
            data: null,
            error: {
              code: "domain_error",
              message_key: "errors.not_impersonating",
            },
          },
          { status: 400 },
        ),
      ),
    );

    renderFullApp(`/admin/users/${target.id}`);
    await submitHandoffForm();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("handoff-deep-link")).not.toBeInTheDocument();
  }, 15000);

  it("revokes the session when the operator discards an unscanned link", async () => {
    const ends: (string | null)[] = [];
    stubAdminApi({ onEnd: (auth) => ends.push(auth) });

    renderFullApp(`/admin/users/${target.id}`);
    await submitHandoffForm();
    await screen.findByTestId("handoff-deep-link");

    await userEvent.click(
      screen.getByRole("button", { name: "Discard session" }),
    );

    // The dialog is the only holder of that token -- a session nobody scanned
    // has no other way to be revoked, so not spending it here would leave one
    // live for up to an hour with nothing able to stop it.
    await waitFor(() => {
      expect(ends).toEqual([`Bearer ${impersonationToken}`]);
    });
  }, 15000);

  it("revokes the previous session before minting a second link", async () => {
    const ends: (string | null)[] = [];
    stubAdminApi({ onEnd: (auth) => ends.push(auth) });

    renderFullApp(`/admin/users/${target.id}`);
    await submitHandoffForm();
    await screen.findByTestId("handoff-deep-link");

    await userEvent.click(screen.getByRole("button", { name: "New link" }));

    // Otherwise every click of "New link" would strand another live session.
    await waitFor(() => {
      expect(ends).toHaveLength(1);
    });
    expect(
      screen.getByRole("button", { name: "Create handoff link" }),
    ).toBeInTheDocument();
  }, 15000);

  it("leaves the session running when the operator is done with the link", async () => {
    const ends: (string | null)[] = [];
    stubAdminApi({ onEnd: (auth) => ends.push(auth) });

    renderFullApp(`/admin/users/${target.id}`);
    await submitHandoffForm();
    await screen.findByTestId("handoff-deep-link");

    await userEvent.click(screen.getByRole("button", { name: "Done" }));

    // "Done" means "I scanned it" -- revoking here would kill the session the
    // operator is at that moment carrying to their phone.
    await waitFor(() => {
      expect(screen.queryByTestId("handoff-deep-link")).not.toBeInTheDocument();
    });
    expect(ends).toEqual([]);
  }, 15000);
});
