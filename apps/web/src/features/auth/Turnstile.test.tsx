import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { errorEnvelope } from "../../test/appShellFixtures";
import { server } from "../../test/server";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: ReactNode }) => (
    <a href="#login">{children}</a>
  ),
  useParams: (options?: {
    select?: (params: { locale: string }) => unknown;
  }) => (options?.select ? options.select({ locale: "en" }) : { locale: "en" }),
}));

const hooks = vi.hoisted(() => ({ useTurnstileSiteKey: vi.fn() }));
vi.mock("@forge/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@forge/hooks")>()),
  useTurnstileSiteKey: hooks.useTurnstileSiteKey,
}));

const { ForgotPasswordPage } = await import("./ForgotPasswordPage");

const API = getApiBaseUrl();

interface RenderOptions {
  sitekey: string;
  callback: (token: string) => void;
}

// Stands in for Cloudflare's script: records each render, so a test can
// "pass" the check by calling the callback Turnstile would.
const rendered: RenderOptions[] = [];

beforeEach(() => {
  rendered.length = 0;
  window.turnstile = {
    render: (_element, options) => {
      rendered.push(options);
      return `widget-${String(rendered.length)}`;
    },
    remove: vi.fn(),
  };
});

afterEach(() => {
  delete window.turnstile;
});

async function fillEmail() {
  await userEvent.type(screen.getByLabelText("Email"), "jane@example.com");
}

describe("Turnstile on the forgot-password form", () => {
  it("shows no check and sends no token when it is off", async () => {
    hooks.useTurnstileSiteKey.mockReturnValue(null);
    let body: unknown = null;
    server.use(
      http.post(`${API}/api/v1/auth/forgot-password`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({
          data: null,
          error: null,
          meta: { request_id: "r" },
        });
      }),
    );
    render(<ForgotPasswordPage />);

    expect(screen.queryByTestId("turnstile")).toBeNull();
    await fillEmail();
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(body).toEqual({ email: "jane@example.com" });
    });
  });

  it("waits for the check, then sends its token", async () => {
    hooks.useTurnstileSiteKey.mockReturnValue("site-key");
    let body: unknown = null;
    server.use(
      http.post(`${API}/api/v1/auth/forgot-password`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({
          data: null,
          error: null,
          meta: { request_id: "r" },
        });
      }),
    );
    render(<ForgotPasswordPage />);
    await fillEmail();
    const submit = screen.getByRole("button", { name: /send/i });

    await waitFor(() => {
      expect(rendered).toHaveLength(1);
    });
    expect(rendered[0]?.sitekey).toBe("site-key");
    expect(submit).toBeDisabled();

    rendered[0]?.callback("token-from-cloudflare");
    await waitFor(() => {
      expect(submit).toBeEnabled();
    });
    await userEvent.click(submit);

    await waitFor(() => {
      expect(body).toEqual({
        email: "jane@example.com",
        captcha_token: "token-from-cloudflare",
      });
    });
  });

  it("shows a fresh check after a refused submit", async () => {
    hooks.useTurnstileSiteKey.mockReturnValue("site-key");
    server.use(
      http.post(`${API}/api/v1/auth/forgot-password`, () =>
        HttpResponse.json(errorEnvelope("failed", "captcha_failed"), {
          status: 422,
        }),
      ),
    );
    render(<ForgotPasswordPage />);
    await fillEmail();
    await waitFor(() => {
      expect(rendered).toHaveLength(1);
    });
    rendered[0]?.callback("spent-token");
    const submit = screen.getByRole("button", { name: /send/i });
    await waitFor(() => {
      expect(submit).toBeEnabled();
    });

    await userEvent.click(submit);

    await waitFor(() => {
      expect(rendered).toHaveLength(2);
    });
    expect(submit).toBeDisabled();
  });
});
