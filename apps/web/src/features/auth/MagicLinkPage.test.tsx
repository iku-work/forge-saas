import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import { envelope, fixtureUser } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

/**
 * Replace `window.location` with something that records `assign` calls.
 *
 * `defineProperty` rather than spreading the real `Location` onto a literal:
 * it is a class instance, so a spread drops its prototype, and eslint stops
 * that on sight. Restored in `afterEach` -- a leaked stub would make every
 * later test in this file navigate into a mock.
 */
let originalLocation: PropertyDescriptor | undefined;

function stubNavigation(): ReturnType<typeof vi.fn> {
  originalLocation ??= Object.getOwnPropertyDescriptor(window, "location");
  const assign = vi.fn();
  Object.defineProperty(window, "location", { configurable: true, value: { assign } });
  return assign;
}

afterEach(() => {
  if (originalLocation) {
    Object.defineProperty(window, "location", originalLocation);
  }
});

/**
 * Consuming a magic link, including the case the backend only started
 * returning after the MFA bypass was closed.
 *
 * A magic link proves control of an inbox. For an account with a second
 * factor enrolled that is one factor, not two -- but this endpoint used to
 * answer with a full session regardless, so the enrolled factor was never
 * asked for. It now answers with the same challenge `/login` does, and this
 * page has to finish it rather than navigating to a dashboard the user has
 * not earned yet.
 */
describe("MagicLinkPage consume", () => {
  it("shows the two-factor challenge when the link alone is not enough", async () => {
    server.use(
      http.post(`${API}/api/v1/auth/magic-link/consume`, () =>
        HttpResponse.json(
          envelope({ pending_2fa: true, temp_token: "temp-token-123" }),
        ),
      ),
    );
    // The success path is a document navigation, so a test that reached it
    // by mistake would otherwise look like a pass.
    const assign = stubNavigation();

    renderApp({ initialPath: "/magic-link?token=abc" });

    expect(
      await screen.findByRole("heading", {
        name: t("auth.two_factor_challenge.title", {}, "en"),
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(t("auth.two_factor_challenge.code_label", {}, "en")),
    ).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("signs in directly when the account has no second factor", async () => {
    // The converse: the new branch must not turn every magic-link sign-in
    // into a challenge.
    server.use(
      http.post(`${API}/api/v1/auth/magic-link/consume`, () =>
        HttpResponse.json(
          envelope({ access_token: "access-token", user: fixtureUser }),
        ),
      ),
    );
    const assign = stubNavigation();

    renderApp({ initialPath: "/magic-link?token=abc" });

    await vi.waitFor(() => {
      expect(assign).toHaveBeenCalledWith("/");
    });
    expect(
      screen.queryByRole("heading", {
        name: t("auth.two_factor_challenge.title", {}, "en"),
      }),
    ).not.toBeInTheDocument();
  });
});
