import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureConfig,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "../test/appShellFixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

// T-263. Its own file for the reason documented in GlobalBanner.test.tsx: the
// config store memoizes one /config response per module, and vitest isolates
// modules per test file.

const API = getApiBaseUrl();
const user = makeUser(46);

describe("maintenance mode (web)", () => {
  beforeEach(() => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(envelope([fixtureOrg])),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            maintenance: { enabled: true, message: "Back at 03:00 UTC." },
          }),
        ),
      ),
    );
  });

  it("replaces the app shell entirely", async () => {
    renderApp({ initialPath: "/", user });

    const screenEl = await screen.findByTestId("maintenance-screen");
    // The operator's own copy, rendered verbatim rather than translated.
    expect(screenEl).toHaveTextContent("Back at 03:00 UTC.");
    // Not *alongside* the shell: every request the shell makes answers 503,
    // so rendering it produces a wall of error states on a half-built page.
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
  });

  it("leaves the admin surface reachable", async () => {
    // The admin route tree is separate and deliberately not gated -- it is
    // the surface that ends the window, and a maintenance screen there would
    // lock the operator out of their own switch.
    renderApp({
      initialPath: "/admin",
      user: makeUser(47, { is_superadmin: true }),
    });

    expect(
      await screen.findByRole("heading", { name: "Admin dashboard" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("maintenance-screen")).not.toBeInTheDocument();
  });
});
