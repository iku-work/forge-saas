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
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    expect(
      await screen.findByRole("heading", { name: "Check your inbox" }),
    ).toBeInTheDocument();
    expect(registerBody).toEqual({
      email: "jane@example.com",
      password: "CorrectHorse123",
    });
  });
});
