import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureSession,
  makeUser,
} from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({ useAuth: vi.fn(), useModules: vi.fn() }));
vi.mock("@forge/hooks", () => hooks);

const { SecuritySettingsPage } = await import("./SecuritySettingsPage");

const API = getApiBaseUrl();
const user = makeUser(5);

const allModulesEnabled = {
  billing: true,
  notifications: true,
  fileStorage: true,
  featureFlags: true,
  gdpr: true,
  admin: true,
  oauth: true,
  totp2fa: true,
  multiOrg: true,
  usageMetering: true,
  outboundWebhooks: true,
};

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <SecuritySettingsPage />
    </QueryClientProvider>,
  );
}

describe("SecuritySettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.useModules.mockReturnValue(allModulesEnabled);
  });

  it("shows an empty sessions state and the Enable 2FA action for a user without 2FA", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([])),
      ),
    );

    renderPage();

    expect(await screen.findByText("No active sessions.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Enable 2FA" }),
    ).toBeInTheDocument();
  });

  it("lists active sessions with a revoke action", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([fixtureSession])),
      ),
    );

    renderPage();

    expect(await screen.findByText("Chrome on macOS")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("shows the disable form when the user already has 2FA enabled", async () => {
    hooks.useAuth.mockReturnValue({
      user: { ...user, totp_enabled: true },
      isLoading: false,
    });
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([])),
      ),
    );

    renderPage();

    expect(
      await screen.findByText("Two-factor authentication is enabled."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Disable 2FA" }),
    ).toBeInTheDocument();
  });

  it("hides the TOTP section when the totp_2fa module is disabled", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });
    hooks.useModules.mockReturnValue({ ...allModulesEnabled, totp2fa: false });
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([])),
      ),
    );

    renderPage();

    expect(await screen.findByText("No active sessions.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Enable 2FA" }),
    ).not.toBeInTheDocument();
  });
});
