import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

describe("RegisterPage", () => {
  it("blocks submission when the password is too short", async () => {
    let registerCalled = false;
    server.use(
      http.post(`${API}/api/v1/auth/register`, () => {
        registerCalled = true;
        return HttpResponse.json(envelope(null));
      }),
    );
    const user = userEvent.setup();
    renderApp({ initialPath: "/register" });
    await screen.findByRole("heading", { name: "Create your account" });

    await user.type(screen.getByLabelText("Email"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "short1A");
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    expect(
      await screen.findByText(/password must be at least 12 characters/i),
    ).toBeInTheDocument();
    expect(registerCalled).toBe(false);
  });

  it("blocks submission when the repeated password differs", async () => {
    let registerCalled = false;
    server.use(
      http.post(`${API}/api/v1/auth/register`, () => {
        registerCalled = true;
        return HttpResponse.json(envelope(null));
      }),
    );
    const user = userEvent.setup();
    renderApp({ initialPath: "/register" });
    await screen.findByRole("heading", { name: "Create your account" });

    await user.type(screen.getByLabelText("Email"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "CorrectHorse123");
    await user.type(
      screen.getByLabelText("Repeat password"),
      "CorrectHorse124",
    );
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    expect(
      await screen.findByText("Passwords do not match"),
    ).toBeInTheDocument();
    expect(registerCalled).toBe(false);
  });

  it("calls the register API and shows the check-your-inbox state on success", async () => {
    let registerBody: unknown = null;
    server.use(
      http.post(`${API}/api/v1/auth/register`, async ({ request }) => {
        registerBody = await request.json();
        return HttpResponse.json(envelope(null));
      }),
    );
    const user = userEvent.setup();
    renderApp({ initialPath: "/register" });
    await screen.findByRole("heading", { name: "Create your account" });

    await user.type(screen.getByLabelText("Email"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "CorrectHorse123");
    await user.type(
      screen.getByLabelText("Repeat password"),
      "CorrectHorse123",
    );
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    expect(
      await screen.findByRole("heading", { name: "Check your inbox" }),
    ).toBeInTheDocument();
    expect(registerBody).toEqual({
      email: "jane@example.com",
      password: "CorrectHorse123",
    });
  });

  // UX-02: the terms document asserts that creating an account constitutes
  // acceptance of it, and this screen linked to neither it nor the privacy
  // policy. An agreement nobody was shown is not one.

  it("links to the terms and the privacy policy before you agree to them", async () => {
    renderApp({ initialPath: "/register" });
    await screen.findByRole("heading", { name: "Create your account" });

    const terms = screen.getByRole("link", { name: "Terms of Service" });
    const privacy = screen.getByRole("link", { name: "Privacy Policy" });

    // The marketing site is a different origin (T-307), so these are
    // absolute and locale-prefixed exactly as that site routes them.
    expect(terms).toHaveAttribute(
      "href",
      "https://marketing.localhost/en/terms",
    );
    expect(privacy).toHaveAttribute(
      "href",
      "https://marketing.localhost/en/privacy",
    );
    expect(terms).toHaveAttribute("target", "_blank");
    expect(terms).toHaveAttribute("rel", "noreferrer");
  });
});
