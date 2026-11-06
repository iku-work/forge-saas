import { http, HttpResponse } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import {
  authRetryInterceptor,
  getApiBaseUrl,
  setReauthHandler,
} from "@forge/api-client";
import { t } from "@forge/i18n";

import { envelope, errorEnvelope } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({
  useAuth: vi.fn(() => ({
    user: { has_password: true, totp_enabled: false },
    logout: vi.fn(),
  })),
}));
vi.mock("@forge/hooks", () => hooks);

const { ReauthDialog } = await import("./ReauthDialog");
const { requestReauth } = await import("./reauthPrompt");

const API = getApiBaseUrl();
const PORTAL = `${API}/api/v1/billing/portal-session`;

const stepUp = () =>
  HttpResponse.json(errorEnvelope("old sign-in", "step_up_auth_required"), {
    status: 403,
  }).clone();

// The interceptor's own options shape; only serializedBody is read.
const options = { serializedBody: undefined } as unknown as Parameters<
  typeof authRetryInterceptor
>[2];

function portalRequest(): Promise<Response> {
  return authRetryInterceptor(
    stepUp(),
    new Request(PORTAL, { method: "POST" }),
    options,
  );
}

describe("ReauthDialog", () => {
  // What AppRoot does while someone is signed in.
  beforeAll(() => {
    setReauthHandler(requestReauth);
  });
  afterAll(() => {
    setReauthHandler(null);
  });

  it("answers a request made before it had loaded", async () => {
    server.use(
      http.get(`${API}/api/v1/auth/oauth/accounts`, () =>
        HttpResponse.json(envelope([])),
      ),
    );

    const result = portalRequest();
    // Let the interceptor read the 403 and ask, with no dialog mounted yet.
    await new Promise((resolve) => setTimeout(resolve, 0));
    render(<ReauthDialog />);
    await userEvent.click(
      await screen.findByRole("button", { name: t("auth.reauth.cancel") }),
    );

    expect((await result).status).toBe(403);
  });

  it("asks for the password, then lets the blocked request through", async () => {
    let sentPassword: unknown = null;
    server.use(
      http.get(`${API}/api/v1/auth/oauth/accounts`, () =>
        HttpResponse.json(envelope([])),
      ),
      http.post(`${API}/api/v1/auth/reauth`, async ({ request }) => {
        sentPassword = ((await request.json()) as { password?: string })
          .password;
        return HttpResponse.json(envelope({ access_token: "fresh-token" }));
      }),
      http.post(PORTAL, ({ request }) =>
        request.headers.get("Authorization") === "Bearer fresh-token"
          ? HttpResponse.json(envelope({ url: "https://portal.example" }))
          : HttpResponse.json(errorEnvelope("no"), { status: 403 }),
      ),
    );
    render(<ReauthDialog />);

    const result = portalRequest();
    await userEvent.type(
      await screen.findByLabelText(t("auth.reauth.password_label")),
      "Correct-Horse9",
    );
    await userEvent.click(
      screen.getByRole("button", { name: t("auth.reauth.submit") }),
    );

    expect((await result).status).toBe(200);
    expect(sentPassword).toBe("Correct-Horse9");
  });

  it("shows a wrong password and keeps the dialog open", async () => {
    server.use(
      http.get(`${API}/api/v1/auth/oauth/accounts`, () =>
        HttpResponse.json(envelope([])),
      ),
      http.post(`${API}/api/v1/auth/reauth`, () =>
        HttpResponse.json(
          {
            ...errorEnvelope("wrong", "authentication_error"),
            error: {
              code: "authentication_error",
              message: "wrong",
              message_key: "errors.reauth_failed",
            },
          },
          { status: 401 },
        ),
      ),
    );
    render(<ReauthDialog />);

    void portalRequest();
    await userEvent.type(
      await screen.findByLabelText(t("auth.reauth.password_label")),
      "wrong",
    );
    await userEvent.click(
      screen.getByRole("button", { name: t("auth.reauth.submit") }),
    );

    expect(
      await screen.findByText(t("errors.reauth_failed")),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: t("auth.reauth.title") }),
    ).toBeInTheDocument();
  });
});
