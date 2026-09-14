import { waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeUser } from "../test/appShellFixtures";
import { renderApp } from "../test/render";

// `_app` bounces signed-out visitors to /login, but nothing bounced signed-in
// ones the other way, so opening the app in a second tab and clicking "sign
// in" showed a login form to somebody who was already authenticated. The
// session was fine -- the access token lives in memory and AppRoot restores
// it from the httpOnly refresh cookie before the router renders -- the page
// simply never asked.
//
// Worth guarding rather than tidying: logging in again mints a new session
// and rotates the refresh cookie, which can sign out the tab you were
// happily using, and the auth endpoints are rate limited to 5/minute per IP,
// so a couple of pointless logins can lock you out of a real one.
//
// Exercised through the real router, from URLs a person actually arrives at.

const user = makeUser(40);

describe("public auth routes, signed in", () => {
  it.each([
    ["/en/login"],
    ["/en/forgot-password"],
    ["/en/reset-password"],
    ["/en/magic-link"],
  ])("redirects %s to the app", async (path) => {
    const { router } = renderApp({ initialPath: path, user });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en");
    });
  });

  it.each([
    ["/en/reset-password?token=abc", "/en/reset-password"],
    ["/en/magic-link?token=abc", "/en/magic-link"],
  ])("leaves %s alone, because the token may be another account's", async (
    path,
    expected,
  ) => {
    // Following an emailed link while signed in as somebody else is a real
    // thing to do, and redirecting away would consume nothing and strand
    // the token. Only the bare, nothing-to-do visit redirects.
    const { router } = renderApp({ initialPath: path, user });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(expected);
    });
    expect(router.state.location.searchStr).toBe("?token=abc");
  });
});

describe("public auth routes, signed out", () => {
  it.each([
    ["/en/login"],
    ["/en/forgot-password"],
    ["/en/reset-password"],
    ["/en/magic-link"],
  ])("still renders %s", async (path) => {
    // The other direction of the same guard: it must not lock out the people
    // these pages exist for.
    const { router } = renderApp({ initialPath: path, user: null });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(path.split("?")[0]);
    });
  });
});
