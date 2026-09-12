import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
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
 * The shell's user menu showed initials even for a user with an uploaded
 * avatar: the signed-URL query lived inline in ProfileSettingsPage, so that
 * page was the only surface in the app that ever rendered the picture. Both
 * now share useAvatarSrc.
 *
 * Asserted on the request, not on a rendered <img>: Radix's AvatarImage only
 * mounts one once the browser reports the image loaded, and jsdom never
 * loads images -- the same reason ProfileSettingsPage.test.tsx tests it this
 * way. What matters is that the shell asks at all, which it previously did
 * not.
 */
describe("AppLayout user menu avatar", () => {
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

  it("fetches the signed avatar URL for the shell, not just the profile page", async () => {
    const user = makeUser(1, { has_uploaded_avatar: true });
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
        return HttpResponse.json(
          envelope({
            url: "https://storage.example.test/forge/a.jpg?X-Amz-Signature=abc",
            expires_at: "2026-12-31T00:00:00Z",
          }),
        );
      }),
    );

    // The dashboard, not the profile page -- the point is that the shell
    // alone is enough to trigger the fetch.
    renderApp({ initialPath: "/", user });

    expect(await screen.findByTestId("user-menu-avatar")).toBeInTheDocument();
    await waitFor(() => {
      expect(asked).toBe(true);
    });
  });
});
