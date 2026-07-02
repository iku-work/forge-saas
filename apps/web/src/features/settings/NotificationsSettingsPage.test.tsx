import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { fixtureUser } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({ useAuth: vi.fn() }));
vi.mock("@forge/hooks", () => hooks);

const { NotificationsSettingsPage } = await import("./NotificationsSettingsPage");

const API = getApiBaseUrl();

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationsSettingsPage />
    </QueryClientProvider>,
  );
}

describe("NotificationsSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the matrix pre-filled with the backend's defaults when the user has never saved preferences", () => {
    hooks.useAuth.mockReturnValue({ user: fixtureUser, isLoading: false });

    renderPage();

    // payment_failed defaults to email=true; member_invited defaults to push=false.
    expect(
      screen.getByRole("checkbox", { name: "A payment fails Email" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", {
        name: "Someone joins your organization Push",
      }),
    ).not.toBeChecked();
  });

  it("merges a partially-saved row with defaults for the channels it never touched", () => {
    hooks.useAuth.mockReturnValue({
      user: {
        ...fixtureUser,
        notification_preferences: { weekly_summary: { email: false } },
      },
      isLoading: false,
    });

    renderPage();

    // The saved override wins for email; in_app/push fall back to
    // DEFAULT_NOTIFICATION_PREFERENCES.weekly_summary since the user never
    // touched them (both default false there).
    expect(
      screen.getByRole("checkbox", { name: "Weekly summary Email" }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Weekly summary In-app" }),
    ).not.toBeChecked();
  });

  it("toggling a channel and saving sends the complete preferences payload", async () => {
    hooks.useAuth.mockReturnValue({ user: fixtureUser, isLoading: false });
    let savedBody: unknown = null;
    server.use(
      http.patch(`${API}/api/v1/me/notification-preferences`, async ({ request }) => {
        savedBody = await request.json();
        return HttpResponse.json({
          data: savedBody,
          error: null,
          meta: { request_id: "req-1" },
        });
      }),
    );

    renderPage();

    await userEvent.click(
      screen.getByRole("checkbox", { name: "A payment fails Email" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save preferences" }));

    expect(await screen.findByText("Preferences saved.")).toBeInTheDocument();
    expect(savedBody).toEqual({
      preferences: {
        member_invited: { email: true, in_app: true, push: false },
        payment_failed: { email: false, in_app: true, push: true },
        subscription_expiring: { email: true, in_app: true, push: false },
        weekly_summary: { email: true, in_app: false, push: false },
        file_uploaded: { email: false, in_app: true, push: false },
      },
    });
  });

  it("shows an error message when saving fails", async () => {
    hooks.useAuth.mockReturnValue({ user: fixtureUser, isLoading: false });
    server.use(
      http.patch(`${API}/api/v1/me/notification-preferences`, () =>
        HttpResponse.json(
          { data: null, error: { code: "internal_error", message: "boom" }, meta: { request_id: "req-1" } },
          { status: 500 },
        ),
      ),
    );

    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "Save preferences" }));

    expect(
      await screen.findByText("Couldn't save your preferences."),
    ).toBeInTheDocument();
  });
});
