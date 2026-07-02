import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => secureStore);

const mocks = vi.hoisted(() => ({
  listMyOrgsApiV1OrgsGet: vi.fn(),
  listNotificationsApiV1NotificationsGet: vi.fn(),
  markNotificationReadApiV1NotificationsNotificationIdReadPatch: vi.fn(),
  markAllNotificationsReadApiV1NotificationsReadAllPost: vi.fn(),
  router: { push: vi.fn() },
}));

vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return {
    ...actual,
    listMyOrgsApiV1OrgsGet: mocks.listMyOrgsApiV1OrgsGet,
    listNotificationsApiV1NotificationsGet:
      mocks.listNotificationsApiV1NotificationsGet,
    markNotificationReadApiV1NotificationsNotificationIdReadPatch:
      mocks.markNotificationReadApiV1NotificationsNotificationIdReadPatch,
    markAllNotificationsReadApiV1NotificationsReadAllPost:
      mocks.markAllNotificationsReadApiV1NotificationsReadAllPost,
  };
});
vi.mock("expo-router", () => ({ router: mocks.router }));

const { NotificationsScreen } = await import("./NotificationsScreen");

const ORG = { id: "org-1", name: "Acme", slug: "acme" };

const UNREAD_NOTIFICATION = {
  id: "n1",
  type: "member_invited",
  title: "Jane joined",
  body: "Jane joined your org",
  action_url: null,
  channels: ["in_app"],
  read_at: null,
  created_at: new Date().toISOString(),
};

const READ_NOTIFICATION = {
  id: "n2",
  type: "payment_failed",
  title: "Payment failed",
  body: "Update your card",
  action_url: "/settings",
  channels: ["in_app"],
  read_at: "2026-01-01T00:00:00Z",
  created_at: new Date().toISOString(),
};

function renderScreen() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationsScreen />
    </QueryClientProvider>,
  );
}

describe("NotificationsScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listMyOrgsApiV1OrgsGet.mockResolvedValue({
      data: { data: [ORG], error: null, meta: { request_id: "r1" } },
    });
    mocks.listNotificationsApiV1NotificationsGet.mockResolvedValue({
      data: {
        data: [READ_NOTIFICATION, UNREAD_NOTIFICATION],
        error: null,
        meta: { request_id: "r2", next_cursor: null },
      },
    });
    mocks.markNotificationReadApiV1NotificationsNotificationIdReadPatch.mockResolvedValue(
      {
        data: {
          data: { ...UNREAD_NOTIFICATION, read_at: new Date().toISOString() },
          error: null,
        },
      },
    );
    mocks.markAllNotificationsReadApiV1NotificationsReadAllPost.mockResolvedValue(
      {
        data: { data: { updated: 1 }, error: null },
      },
    );
  });

  it("lists notifications and lets the user mark one as read", async () => {
    const user = userEvent.setup();
    renderScreen();

    expect(await screen.findByText("Jane joined")).toBeTruthy();
    expect(screen.getByText("Payment failed")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Mark as read" }));

    await waitFor(() => {
      expect(
        mocks.markNotificationReadApiV1NotificationsNotificationIdReadPatch,
      ).toHaveBeenCalledWith(
        expect.objectContaining({ path: { notification_id: "n1" } }),
      );
    });
  });

  it("marks all notifications as read", async () => {
    const user = userEvent.setup();
    renderScreen();

    await screen.findByText("Jane joined");
    await user.click(screen.getByRole("button", { name: "Mark all as read" }));

    await waitFor(() => {
      expect(
        mocks.markAllNotificationsReadApiV1NotificationsReadAllPost,
      ).toHaveBeenCalled();
    });
  });

  it("navigates using the notification's action_url when View is tapped", async () => {
    const user = userEvent.setup();
    renderScreen();

    await screen.findByText("Payment failed");
    await user.click(screen.getByRole("link", { name: "View" }));

    expect(mocks.router.push).toHaveBeenCalledWith("/settings");
  });

  it("shows the empty state when there are no notifications", async () => {
    mocks.listNotificationsApiV1NotificationsGet.mockResolvedValue({
      data: {
        data: [],
        error: null,
        meta: { request_id: "r3", next_cursor: null },
      },
    });

    renderScreen();

    expect(await screen.findByText("You're all caught up.")).toBeTruthy();
  });
});
