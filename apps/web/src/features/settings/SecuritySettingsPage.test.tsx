import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

const webauthnBrowser = vi.hoisted(() => ({ startRegistration: vi.fn() }));
vi.mock("@simplewebauthn/browser", () => webauthnBrowser);

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

  it("submits a password change and shows the success message", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([])),
      ),
    );
    const interaction = userEvent.setup();

    renderPage();

    // Scoped to this card's own <form> -- ChangeEmailCard has its own
    // "Current password" field on the same page (T-226), so an unscoped
    // getByLabelText("Current password") would match both.
    const newPasswordField = screen.getByLabelText("New password");
    const passwordForm = newPasswordField.closest("form");
    if (!passwordForm) {
      throw new Error("Change password form not found");
    }
    const passwordFormScope = within(passwordForm);

    await interaction.type(
      passwordFormScope.getByLabelText("Current password"),
      "Correct-Horse9",
    );
    await interaction.type(newPasswordField, "Even-Better-Horse8");
    await interaction.click(
      passwordFormScope.getByRole("button", { name: "Change password" }),
    );

    expect(
      await screen.findByText(
        "Your password has been changed. Other devices have been signed out.",
      ),
    ).toBeInTheDocument();
  });

  it("lists linked OAuth accounts and unlinks a provider", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });
    let unlinkCalled = false;
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([])),
      ),
      http.get(`${API}/api/v1/auth/oauth/accounts`, () =>
        HttpResponse.json(
          envelope([
            { provider: "google", created_at: "2026-01-01T00:00:00Z" },
          ]),
        ),
      ),
      http.post(`${API}/api/v1/auth/oauth/google/unlink`, () => {
        unlinkCalled = true;
        return HttpResponse.json(envelope(null));
      }),
    );
    const interaction = userEvent.setup();

    renderPage();

    await screen.findByText("Google");
    await interaction.click(screen.getByRole("button", { name: "Unlink" }));

    await waitFor(() => {
      expect(unlinkCalled).toBe(true);
    });
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
    expect(
      screen.queryByRole("button", { name: "Register a security key" }),
    ).not.toBeInTheDocument();
  });

  it("lists registered security keys with a revoke action", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([])),
      ),
      http.get(`${API}/api/v1/auth/webauthn/credentials`, () =>
        HttpResponse.json(
          envelope([
            {
              id: "cred-1",
              nickname: "YubiKey",
              aaguid: "00000000-0000-0000-0000-000000000000",
              created_at: "2026-01-01T00:00:00Z",
              last_used_at: null,
            },
          ]),
        ),
      ),
    );

    renderPage();

    expect(await screen.findByText("YubiKey")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove" }),
    ).toBeInTheDocument();
  });

  it("registers a new security key", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });
    webauthnBrowser.startRegistration.mockResolvedValue({
      id: "new-cred",
      rawId: "new-cred",
      type: "public-key",
      response: { clientDataJSON: "cdj", attestationObject: "ao" },
    });
    let registerCalled = false;
    server.use(
      http.get(`${API}/api/v1/auth/sessions`, () =>
        HttpResponse.json(envelope([])),
      ),
      http.get(`${API}/api/v1/auth/webauthn/credentials`, () =>
        HttpResponse.json(envelope([])),
      ),
      http.post(`${API}/api/v1/auth/webauthn/register`, () => {
        registerCalled = true;
        return HttpResponse.json(
          envelope({
            id: "new-cred",
            nickname: null,
            aaguid: "00000000-0000-0000-0000-000000000000",
            created_at: "2026-01-01T00:00:00Z",
            last_used_at: null,
          }),
        );
      }),
    );
    const interaction = userEvent.setup();

    renderPage();

    await screen.findByText("No security keys registered.");
    await interaction.click(
      screen.getByRole("button", { name: "Register a security key" }),
    );

    await waitFor(() => {
      expect(registerCalled).toBe(true);
    });
  });
});
