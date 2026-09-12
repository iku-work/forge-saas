import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "../test/appShellFixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

const API = getApiBaseUrl();

/**
 * The counterpart to AppLayoutUserMenuAvatar.test.tsx: a user with no
 * uploaded avatar must cost no request at all, since /auth/me/avatar
 * presigns on every call.
 *
 * Its own file rather than a second `it` alongside that one: the app-shell
 * bootstrap is module-level, so two scenarios in one file share it and the
 * first test's fetch leaks into the second (see appShellFixtures' note).
 */
describe("AppLayout user menu avatar (no uploaded avatar)", () => {
  beforeEach(() => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(envelope([fixtureOrg])),
      ),
    );
  });

  it("does not ask when the user has no uploaded avatar", async () => {
    const user = makeUser(1, { has_uploaded_avatar: false });
    let asked = false;
    server.use(
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
      http.get(`${API}/api/v1/auth/me/avatar`, () => {
        asked = true;
        return HttpResponse.json(envelope({ url: "x", expires_at: "y" }));
      }),
    );

    renderApp({ initialPath: "/", user });

    expect(await screen.findByTestId("user-menu-avatar")).toBeInTheDocument();
    expect(asked).toBe(false);
  });
});
