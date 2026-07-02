import { screen } from "@testing-library/react";
import { describe, it } from "vitest";

import { expectNoA11yViolations } from "../../test/axe";
import { renderApp } from "../../test/render";

describe("auth pages a11y", () => {
  it("login page has no a11y violations", async () => {
    const { container } = renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", { name: "Log in" });
    await expectNoA11yViolations(container);
  });

  it("register page has no a11y violations", async () => {
    const { container } = renderApp({ initialPath: "/register" });
    await screen.findByRole("heading", { name: "Create your account" });
    await expectNoA11yViolations(container);
  });

  it("verify-email page (no token) has no a11y violations", async () => {
    const { container } = renderApp({ initialPath: "/verify-email" });
    await screen.findByRole("heading", { name: "Verification failed" });
    await expectNoA11yViolations(container);
  });

  it("forgot-password page has no a11y violations", async () => {
    const { container } = renderApp({ initialPath: "/forgot-password" });
    await screen.findByRole("heading", { name: "Reset your password" });
    await expectNoA11yViolations(container);
  });

  it("reset-password page (no token) has no a11y violations", async () => {
    const { container } = renderApp({ initialPath: "/reset-password" });
    await screen.findByRole("heading", { name: "Invalid link" });
    await expectNoA11yViolations(container);
  });

  it("magic-link page has no a11y violations", async () => {
    const { container } = renderApp({ initialPath: "/magic-link" });
    await screen.findByRole("heading", { name: "Sign in with a magic link" });
    await expectNoA11yViolations(container);
  });

  it("accept-invitation page has no a11y violations", async () => {
    // The default MSW handler for GET /api/v1/invitations/:token already
    // returns a fixture invitation preview -- no override needed.
    const { container } = renderApp({
      initialPath: "/accept-invitation/some-token",
    });
    await screen.findByRole("heading", { name: "You're invited" });
    await expectNoA11yViolations(container);
  });
});
