import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import { envelope, fixtureUser } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

describe("LoginPage", () => {
  it("shows a validation error for an invalid email and does not call the API", async () => {
    let loginCalled = false;
    server.use(
      http.post(`${API}/api/v1/auth/login`, () => {
        loginCalled = true;
        return HttpResponse.json(
          envelope({ access_token: "token", user: fixtureUser }),
        );
      }),
    );
    const user = userEvent.setup();
    renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", {
      name: t("auth.login.title", {}, "en"),
    });

    await user.type(
      screen.getByLabelText(t("auth.login.email_label", {}, "en")),
      "not-an-email",
    );
    await user.type(
      screen.getByLabelText(t("auth.login.password_label", {}, "en")),
      "irrelevant-password",
    );
    await user.click(
      screen.getByRole("button", { name: t("auth.login.submit", {}, "en") }),
    );

    expect(
      await screen.findByText(t("validation.email_invalid", {}, "en")),
    ).toBeInTheDocument();
    expect(loginCalled).toBe(false);
  });

  it("calls the login API when the form is valid", async () => {
    let loginBody: unknown = null;
    server.use(
      http.post(`${API}/api/v1/auth/login`, async ({ request }) => {
        loginBody = await request.json();
        return HttpResponse.json(
          envelope({ access_token: "access-token", user: fixtureUser }),
        );
      }),
    );
    const user = userEvent.setup();
    renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", {
      name: t("auth.login.title", {}, "en"),
    });

    await user.type(
      screen.getByLabelText(t("auth.login.email_label", {}, "en")),
      "jane@example.com",
    );
    await user.type(
      screen.getByLabelText(t("auth.login.password_label", {}, "en")),
      "correct-password-123",
    );
    await user.click(
      screen.getByRole("button", { name: t("auth.login.submit", {}, "en") }),
    );

    await waitFor(() => {
      expect(loginBody).toEqual({
        email: "jane@example.com",
        password: "correct-password-123",
      });
    });
  });
});
