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

  it("keeps asking while the avatar's scan is pending, instead of giving up (R7)", async () => {
    // An avatar can be chosen before its malware scan finishes; until then
    // the server answers 409 file_scan_pending. That is "not yet": the
    // shell shows initials and asks again rather than treating the avatar
    // as broken.
    const user = makeUser(1, { has_uploaded_avatar: true });
    let asked = 0;
    server.use(
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
      http.get(`${API}/api/v1/auth/me/avatar`, () => {
        asked += 1;
        if (asked === 1) {
          return HttpResponse.json(
            {
              data: null,
              error: {
                code: "conflict",
                message: "This file is still being scanned for malware.",
                message_key: "errors.file_scan_pending",
              },
              meta: { request_id: "req-1" },
            },
            { status: 409 },
          );
        }
        return HttpResponse.json(
          envelope({
            url: "https://storage.example.test/forge/a.jpg?X-Amz-Signature=abc",
            expires_at: "2026-12-31T00:00:00Z",
          }),
        );
      }),
    );

    renderApp({ initialPath: "/", user });

    await waitFor(
      () => {
        expect(asked).toBe(2);
      },
      { timeout: 8_000 },
    );
  }, 15_000);
});
