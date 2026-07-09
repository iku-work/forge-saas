import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import { envelope, fixtureUser } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const webauthnBrowser = vi.hoisted(() => ({ startAuthentication: vi.fn() }));
vi.mock("@simplewebauthn/browser", () => webauthnBrowser);

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

  it("shows the two-factor challenge and completes login with a code", async () => {
    let twoFaLoginBody: unknown = null;
    server.use(
      http.post(`${API}/api/v1/auth/login`, () =>
        HttpResponse.json(
          envelope({ pending_2fa: true, temp_token: "temp-abc" }),
        ),
      ),
      http.post(`${API}/api/v1/auth/2fa/login`, async ({ request }) => {
        twoFaLoginBody = await request.json();
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

    await screen.findByRole("heading", {
      name: t("auth.two_factor_challenge.title", {}, "en"),
    });
    await user.type(
      screen.getByLabelText(t("auth.two_factor_challenge.code_label", {}, "en")),
      "123456",
    );
    await user.click(
      screen.getByRole("button", {
        name: t("auth.two_factor_challenge.submit", {}, "en"),
      }),
    );

    await waitFor(() => {
      expect(twoFaLoginBody).toEqual({ temp_token: "temp-abc", code: "123456" });
    });
  });

  it("completes the two-factor challenge with a security key", async () => {
    webauthnBrowser.startAuthentication.mockResolvedValue({
      id: "cred-1",
      rawId: "cred-1",
      type: "public-key",
      response: {
        clientDataJSON: "cdj",
        authenticatorData: "ad",
        signature: "sig",
      },
    });
    let webauthnLoginBody: unknown = null;
    server.use(
      http.post(`${API}/api/v1/auth/login`, () =>
        HttpResponse.json(
          envelope({ pending_2fa: true, temp_token: "temp-abc" }),
        ),
      ),
      http.post(`${API}/api/v1/auth/webauthn/login`, async ({ request }) => {
        webauthnLoginBody = await request.json();
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

    await screen.findByRole("heading", {
      name: t("auth.two_factor_challenge.title", {}, "en"),
    });
    await user.click(
      screen.getByRole("button", {
        name: t("auth.two_factor_challenge.use_security_key", {}, "en"),
      }),
    );

    await waitFor(() => {
      expect(webauthnLoginBody).toEqual({
        challenge_token: "login-challenge-token",
        credential: {
          id: "cred-1",
          rawId: "cred-1",
          type: "public-key",
          response: {
            clientDataJSON: "cdj",
            authenticatorData: "ad",
            signature: "sig",
          },
        },
      });
    });
  });
});
