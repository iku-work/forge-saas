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
      screen.getByRole("checkbox", { name: "A payment fails Email (required)" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", {
        name: "Someone joins your organization Push",
      }),
    ).not.toBeChecked();
  });

  it("renders a row for file_infected", () => {
    // The virus scanner has sent this type since T-102, but it was missing
    // from NOTIFICATION_TYPES, so the page never offered a row for it.
    hooks.useAuth.mockReturnValue({ user: fixtureUser, isLoading: false });

    renderPage();

    expect(
      screen.getByRole("checkbox", {
        name: "A file you uploaded is flagged as infected Push",
      }),
    ).toBeChecked();
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
      screen.getByRole("checkbox", { name: "A payment fails Push" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save preferences" }));

    expect(await screen.findByText("Preferences saved.")).toBeInTheDocument();
    expect(savedBody).toEqual({
      preferences: {
        member_invited: { email: true, in_app: true, push: false },
        payment_failed: { email: true, in_app: true, push: false },
        subscription_expiring: { email: true, in_app: true, push: false },
        weekly_summary: { email: true, in_app: false, push: false },
        file_uploaded: { email: false, in_app: true, push: false },
        file_infected: { email: true, in_app: true, push: true },
        // T-321's outbound-webhook auto-disable notice.
        webhook_endpoint_disabled: { email: true, in_app: true, push: false },
      },
    });
  });

  describe("channels the user can't switch off (T-234)", () => {
    it("renders the billing email channels checked, disabled, and named as required", () => {
      hooks.useAuth.mockReturnValue({ user: fixtureUser, isLoading: false });

      renderPage();

      for (const name of [
        "A payment fails Email (required)",
        "Your subscription is expiring Email (required)",
      ]) {
        const box = screen.getByRole("checkbox", { name });
        expect(box).toBeChecked();
        expect(box).toBeDisabled();
      }
    });

    it("explains why, via a note the required checkboxes point at", () => {
      hooks.useAuth.mockReturnValue({ user: fixtureUser, isLoading: false });

      renderPage();

      const note = screen.getByText(/always email you about billing problems/i);
      expect(
        screen.getByRole("checkbox", { name: "A payment fails Email (required)" }),
      ).toHaveAttribute("aria-describedby", note.id);
    });

    it("renders a required channel as on even if the stored matrix says off", async () => {
      // The backend's normalize() forces it back on when saved, so a stored
      // `false` can only be a legacy row -- showing it unticked would claim
      // the account behaves in a way it doesn't.
      hooks.useAuth.mockReturnValue({
        user: {
          ...fixtureUser,
          notification_preferences: {
            payment_failed: { email: false, in_app: false, push: false },
          },
        },
        isLoading: false,
      });
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

      expect(
        screen.getByRole("checkbox", { name: "A payment fails Email (required)" }),
      ).toBeChecked();
      // ...and it's what gets submitted, so the stored row self-heals.
      await userEvent.click(screen.getByRole("button", { name: "Save preferences" }));
      await screen.findByText("Preferences saved.");
      expect(savedBody).toMatchObject({
        preferences: { payment_failed: { email: true, in_app: false, push: false } },
      });
    });

    it("stays on when the disabled checkbox is activated anyway", async () => {
      hooks.useAuth.mockReturnValue({ user: fixtureUser, isLoading: false });

      renderPage();
      const box = screen.getByRole("checkbox", {
        name: "A payment fails Email (required)",
      });
      await userEvent.click(box, { pointerEventsCheck: 0 });

      expect(box).toBeChecked();
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
