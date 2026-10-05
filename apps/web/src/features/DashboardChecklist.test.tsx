import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/react";
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
// each user, role or configuration needs its own file. This one is a new
// owner with file storage and billing on; see DashboardChecklistMember,
// DashboardChecklistAllDone and DashboardChecklistNoFiles for the rest.
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

function step(name: string): HTMLElement {
  const list = screen.getByTestId("getting-started");
  const title = within(list).getByText(name);
  const item = title.closest("li");
  if (!item) throw new Error(`no step for ${name}`);
  return item;
}

describe("dashboard Getting started checklist (ticket 10)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows every step a new owner can take, none of them done", async () => {
    const user = given();
    renderApp({ initialPath: "/", user });

    const list = await screen.findByTestId("getting-started");
    for (const title of [
      "Complete your profile",
      "Upload your first file",
      "Invite a member",
      "Choose a plan",
    ]) {
      expect(within(list).getByText(title)).toBeInTheDocument();
    }
    expect(within(list).getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
    // Each open step offers one way to do it.
    expect(
      within(step("Upload your first file")).getByRole("link", {
        name: "Upload file",
      }),
    ).toHaveAttribute("href", "/en/files");
  });

  it("ticks a step off from real data, and counts it", async () => {
    const user = given({ files: 1, invitations: 1 });
    renderApp({ initialPath: "/", user });

    await screen.findByTestId("getting-started");
    const files = step("Upload your first file");
    expect(within(files).getByText("Done")).toBeInTheDocument();
    expect(within(files).queryByRole("link")).not.toBeInTheDocument();
    // An invitation sent counts as inviting someone.
    expect(
      within(step("Invite a member")).getByText("Done"),
    ).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "2",
    );
  });
});
