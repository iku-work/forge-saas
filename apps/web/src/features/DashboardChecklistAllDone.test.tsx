import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureOrg,
  makeMembership,
  makeStoredFile,
  makeUser,
} from "../test/appShellFixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

// @forge/hooks keeps the signed-in user, their role and the product config in
// module-level stores that load once, and vitest isolates modules per *file*:
// each user, role or configuration needs its own file. This one has
// done everything.
const API = getApiBaseUrl();

function given({
  user = makeUser(1),
  role = "owner" as const,
  members = 1,
  files = 0,
  invitations = 0,
  subscriptionStatus = "trialing",
}: {
  user?: ReturnType<typeof makeUser>;
  role?: "owner" | "admin" | "member";
  members?: number;
  files?: number;
  invitations?: number;
  subscriptionStatus?: string;
} = {}) {
  const others = Array.from({ length: members - 1 }, (_, i) => ({
    ...makeMembership(makeUser(10 + i), "member"),
    id: `44444444-4444-4444-4444-${String(i).padStart(12, "0")}`,
  }));
  server.use(
    http.post(`${API}/api/v1/auth/refresh`, () =>
      HttpResponse.json(envelope({ access_token: "token" })),
    ),
    http.get(`${API}/api/v1/auth/me`, () => HttpResponse.json(envelope(user))),
    http.get(`${API}/api/v1/orgs`, () =>
      HttpResponse.json(envelope([fixtureOrg])),
    ),
    http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
      HttpResponse.json(envelope([makeMembership(user, role), ...others])),
    ),
    http.get(`${API}/api/v1/files`, () =>
      HttpResponse.json(
        envelope(
          Array.from({ length: files }, (_, i) => makeStoredFile(i + 1)),
        ),
      ),
    ),
    http.get(`${API}/api/v1/orgs/:orgId/invitations`, () =>
      HttpResponse.json(
        envelope(Array.from({ length: invitations }, () => ({}))),
      ),
    ),
    http.get(`${API}/api/v1/billing/subscription`, () =>
      HttpResponse.json(
        envelope({
          id: "sub_1",
          plan_id: "plan_1",
          status: subscriptionStatus,
          trial_end: null,
          cancel_at_period_end: false,
          current_period_start: "2026-10-01T00:00:00Z",
          current_period_end: "2026-11-01T00:00:00Z",
        }),
      ),
    ),
  );
  return user;
}

describe("dashboard Getting started checklist (ticket 10)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("goes away once everything is done", async () => {
    const user = given({
      user: makeUser(1, { has_uploaded_avatar: true }),
      members: 2,
      files: 1,
      subscriptionStatus: "active",
    });
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByText("Your organization is set up."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("getting-started")).not.toBeInTheDocument();
  });
});
