import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { AdminUserDetail } from "@forge/api-client";

import { envelope } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({ useModules: vi.fn() }));
vi.mock("@forge/hooks", () => hooks);

const { UserModerationSection } = await import("./UserModerationSection");

const API = getApiBaseUrl();
const REASON = "Spam uploads reported by three orgs";

const USER: AdminUserDetail = {
  id: "99999999-9999-9999-9999-000000000001",
  email: "target@example.com",
  full_name: null,
  is_active: true,
  is_superadmin: false,
  created_at: "2026-01-01T00:00:00Z",
  email_verified_at: "2026-01-01T00:00:00Z",
  suspended_at: null,
  banned: false,
  memberships: [],
  sessions: [],
  audit_summary: { total_count: 0, recent_actions: [] },
};

function renderSection(detail: AdminUserDetail) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <UserModerationSection detail={detail} />
    </QueryClientProvider>,
  );
}

function buttons(): string[] {
  return screen.getAllByRole("button").map((button) => button.textContent);
}

describe("UserModerationSection", () => {
  beforeEach(() => {
    hooks.useModules.mockReturnValue({ gdpr: true });
  });

  it("offers every action on an ordinary account", () => {
    renderSection(USER);

    expect(buttons()).toEqual([
      "Sign out everywhere",
      "Suspend",
      "Ban",
      "Delete account",
    ]);
  });

  it("offers no delete without the GDPR module", () => {
    hooks.useModules.mockReturnValue({ gdpr: false });
    renderSection(USER);

    expect(buttons()).not.toContain("Delete account");
  });

  it("shows the state and the way back", () => {
    renderSection({
      ...USER,
      suspended_at: "2026-11-16T00:00:00Z",
      email_verified_at: null,
    });

    expect(screen.getByText("Suspended")).toBeInTheDocument();
    expect(screen.getByText("Email not verified")).toBeInTheDocument();
    expect(buttons()).toEqual([
      "Mark email verified",
      "Sign out everywhere",
      "Unsuspend",
      "Ban",
      "Delete account",
    ]);
  });

  it("offers only unban on a banned account", () => {
    renderSection({
      ...USER,
      banned: true,
      suspended_at: "2026-11-16T00:00:00Z",
    });

    expect(screen.getByText("Banned")).toBeInTheDocument();
    expect(buttons()).not.toContain("Unsuspend");
    expect(buttons()).toContain("Unban");
  });

  it("protects a superadmin", () => {
    renderSection({ ...USER, is_superadmin: true });

    expect(
      screen.getByText(
        "Superadmins can't be suspended, banned, signed out or deleted here.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("asks for a reason before suspending, and sends it", async () => {
    let body: unknown = null;
    server.use(
      http.post(
        `${API}/api/v1/admin/users/:id/suspend`,
        async ({ request }) => {
          body = await request.json();
          return HttpResponse.json(envelope(null));
        },
      ),
    );
    renderSection(USER);

    await userEvent.click(screen.getByRole("button", { name: "Suspend" }));
    const dialog = await screen.findByTestId("moderation-reason-dialog");
    const submit = within(dialog).getByRole("button", { name: "Suspend" });
    expect(dialog).toBeInTheDocument();
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Reason"), REASON);
    await userEvent.click(submit);

    await waitFor(() => {
      expect(body).toEqual({ reason: REASON });
    });
  });

  it("confirms signing out everywhere without a reason", async () => {
    let called = false;
    server.use(
      http.post(`${API}/api/v1/admin/users/:id/sign-out-everywhere`, () => {
        called = true;
        return HttpResponse.json(envelope(null));
      }),
    );
    renderSection(USER);

    await userEvent.click(
      screen.getByRole("button", { name: "Sign out everywhere" }),
    );
    const confirm = await screen.findByTestId("confirm-dialog");
    expect(screen.queryByLabelText("Reason")).toBeNull();
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Sign out everywhere" }),
    );

    await waitFor(() => {
      expect(called).toBe(true);
    });
  });
});
