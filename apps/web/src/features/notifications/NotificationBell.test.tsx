import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { NotificationPublic } from "@forge/api-client";
import type { NotificationStreamHandlers } from "@forge/hooks";

import { envelope, makeNotification } from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import { server } from "../../test/server";

// react-virtual sizes items off real layout measurements (clientHeight,
// getBoundingClientRect) that jsdom always reports as 0 -- without this, the
// virtualizer would render only whatever it thinks fits a zero-height
// viewport, which is not what these tests are exercising. Faking it to
// return every item un-virtualized keeps the assertions about which
// notifications are on screen independent of jsdom's layout limitations.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (options: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: options.count }, (_, index) => ({
        index,
        start: index * 80,
        key: index,
      })),
    getTotalSize: () => options.count * 80,
    measureElement: () => undefined,
  }),
}));

const sse = vi.hoisted(() => ({ subscribeToNotificationStream: vi.fn() }));
vi.mock("../../lib/sse", () => sse);

const { NotificationBell } = await import("./NotificationBell");

const API = getApiBaseUrl();

function renderBell() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationBell />
    </QueryClientProvider>,
  );
}

function mockStream(): { push: (notification: NotificationPublic) => Promise<void> } {
  let handlers: NotificationStreamHandlers | null = null;
  sse.subscribeToNotificationStream.mockImplementation(
    (h: NotificationStreamHandlers) => {
      handlers = h;
      return () => {
        handlers = null;
      };
    },
  );
  return {
    push: async (notification) => {
      // TanStack Query's notifyManager coalesces cache-write notifications
      // onto a microtask, so the subscribed component doesn't re-render
      // within this synchronous callback alone -- the extra microtask tick
      // (and the asserts below using waitFor rather than a single findBy*
      // check) is what actually waits for that re-render to land.
      await act(async () => {
        handlers?.onNotification(notification);
        await Promise.resolve();
      });
    },
  };
}

describe("NotificationBell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sse.subscribeToNotificationStream.mockReturnValue(() => undefined);
  });

  it("renders the unread count on the badge", async () => {
    server.use(
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: 3 })),
      ),
    );

    renderBell();

    expect(
      await screen.findByRole("button", { name: "Notifications, 3 unread" }),
    ).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("inserts a notification pushed over the stream at the top of the feed", async () => {
    const stream = mockStream();
    const existing = makeNotification(10, { title: "Existing notification" });
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(envelope([existing])),
      ),
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: 1 })),
      ),
    );

    renderBell();
    await userEvent.click(
      await screen.findByRole("button", { name: "Notifications, 1 unread" }),
    );
    await screen.findByText("Existing notification");

    await stream.push(makeNotification(11, { title: "Brand new notification" }));

    await waitFor(() => {
      const titles = screen.getAllByText(
        /Existing notification|Brand new notification/,
      );
      expect(titles.map((node) => node.textContent)).toEqual([
        "Brand new notification",
        "Existing notification",
      ]);
    });
  });

  it("marking a notification read clears the unread badge", async () => {
    let unreadCount = 1;
    const notification = makeNotification(20, { title: "Needs attention" });
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(envelope([notification])),
      ),
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: unreadCount })),
      ),
      http.patch(`${API}/api/v1/notifications/:notificationId/read`, () => {
        unreadCount = 0;
        return HttpResponse.json(
          envelope({ ...notification, read_at: "2026-01-02T00:00:00Z" }),
        );
      }),
    );

    renderBell();
    await userEvent.click(
      await screen.findByRole("button", { name: "Notifications, 1 unread" }),
    );
    await screen.findByText("Needs attention");

    await userEvent.click(screen.getByRole("button", { name: "Mark as read" }));

    await screen.findByRole("button", { name: "Notifications" });
    expect(screen.queryByText("1")).not.toBeInTheDocument();
  });

  it("mark all as read clears the unread styling for every row", async () => {
    const first = makeNotification(30, { title: "First" });
    const second = makeNotification(31, { title: "Second" });
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(envelope([first, second])),
      ),
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: 2 })),
      ),
      http.post(`${API}/api/v1/notifications/read-all`, () =>
        HttpResponse.json(envelope({ updated: 2 })),
      ),
    );

    renderBell();
    await userEvent.click(
      await screen.findByRole("button", { name: "Notifications, 2 unread" }),
    );
    await screen.findByText("First");
    expect(screen.getAllByRole("button", { name: "Mark as read" })).toHaveLength(2);

    await userEvent.click(
      screen.getByRole("button", { name: "Mark all as read" }),
    );

    expect(
      screen.queryByRole("button", { name: "Mark as read" }),
    ).not.toBeInTheDocument();
  });

  it("has no a11y violations with the dropdown open", async () => {
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(envelope([makeNotification(40)])),
      ),
    );

    renderBell();
    await userEvent.click(
      await screen.findByRole("button", { name: "Notifications" }),
    );
    await screen.findAllByText(/./);

    // Radix's PopoverContent renders into a body-level portal, outside this
    // render's own container -- scan the full document.
    await expectNoA11yViolations(document.body);
  });
});
