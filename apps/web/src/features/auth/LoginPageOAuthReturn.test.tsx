import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { renderApp } from "../../test/render";

const GENERAL = "We couldn't complete that sign-in. Please try again.";

// What someone sees when Google or an identity provider sends them back:
// backend/app/modules/auth/router.py's browser_sign_in_* helpers.
describe("LoginPage (back from an identity provider)", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("says so when the person cancelled", async () => {
    renderApp({ initialPath: "/login?sign_in_error=cancelled" });

    expect(
      await screen.findByText("Sign-in was cancelled."),
    ).toBeInTheDocument();
  });

  it("explains closed sign-ups", async () => {
    renderApp({ initialPath: "/login?sign_in_error=errors.signup_disabled" });

    expect(
      await screen.findByText(/New account registration is closed right now/),
    ).toBeInTheDocument();
  });

  it("shows the provider's unverified email problem", async () => {
    renderApp({
      initialPath: "/login?sign_in_error=errors.oauth_email_not_verified",
    });

    expect(
      await screen.findByText(
        "Your OAuth provider did not report a verified email address.",
      ),
    ).toBeInTheDocument();
  });

  it.each([
    "failed",
    "errors.oauth_state_mismatch",
    // Links can be typed by anyone: not a route into arbitrary strings, and not
    // a lookup that finds something on Object.prototype.
    "errors.not_a_key",
    "auth.login.title",
    "constructor",
  ])("uses the general message for %s", async (reason) => {
    renderApp({ initialPath: `/login?sign_in_error=${reason}` });

    expect(await screen.findByText(GENERAL)).toBeInTheDocument();
  });

  it("shows a plain login page for a value that is not text", async () => {
    renderApp({ initialPath: "/login?sign_in_error=1" });

    expect(
      await screen.findByRole("heading", { name: "Log in" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(GENERAL)).not.toBeInTheDocument();
  });

  it("shows no error on an ordinary visit", async () => {
    renderApp({ initialPath: "/login" });

    expect(
      await screen.findByRole("heading", { name: "Log in" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("asks for the second factor and takes the token out of the address bar", async () => {
    window.history.replaceState(null, "", "/en/login#temp_token=temp-abc");

    renderApp({ initialPath: "/login" });

    expect(
      await screen.findByRole("heading", {
        name: "Two-factor authentication",
      }),
    ).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/en/login");
  });
});
