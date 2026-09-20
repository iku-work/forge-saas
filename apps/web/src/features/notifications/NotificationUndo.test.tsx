import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { Toaster } from "@forge/ui";

import { envelope, makeNotification } from "../../test/appShellFixtures";
import { server } from "../../test/server";

// Same reasoning as NotificationBell.test.tsx: jsdom reports every layout
// measurement as 0, so the real virtualizer would render nothing.
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
      {/* The undo affordance lives in a toast, so the toast host has to be
          mounted here -- in the app it is rendered once by AppLayout. */}
      <Toaster />
    </QueryClientProvider>,
  );
}

async function openFeed() {
  await userEvent.click(
    await screen.findByRole("button", { name: /Notifications/ }),
  );
  return screen.findByText("Needs attention");
}

describe("mark read / undo (F-008, F-009)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sse.subscribeToNotificationStream.mockReturnValue(() => undefined);
  });

  it("offers an undo instead of asking for confirmation first", async () => {
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(
          envelope([makeNotification(20, { title: "Needs attention" })]),
        ),
      ),
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: 1 })),
      ),
      http.patch(`${API}/api/v1/notifications/:id/read`, () =>
        HttpResponse.json(envelope({})),
      ),
    );

    renderBell();
    await openFeed();
    await userEvent.click(screen.getByRole("button", { name: "Mark as read" }));

    expect(
      await screen.findByRole("button", { name: "Undo" }),
    ).toBeInTheDocument();
  });

  it("calls the unread endpoint when undo is pressed", async () => {
    const unreadCalls: string[] = [];
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(
          envelope([makeNotification(20, { title: "Needs attention" })]),
        ),
      ),
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: 1 })),
      ),
      http.patch(`${API}/api/v1/notifications/:id/read`, () =>
        HttpResponse.json(envelope({})),
      ),
      http.patch(`${API}/api/v1/notifications/:id/unread`, ({ params }) => {
        unreadCalls.push(String(params.id));
        return HttpResponse.json(envelope({}));
      }),
    );

    renderBell();
    await openFeed();
    await userEvent.click(screen.getByRole("button", { name: "Mark as read" }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));

    // The undo hits the server, not just the cache -- "restores state, not
    // only appearance" is the F-009 requirement, and a cache-only undo would
    // survive exactly until the next refetch.
    await waitFor(() => {
      expect(unreadCalls).toHaveLength(1);
    });
  });

  it("marks the row read immediately, without waiting for the server", async () => {
    // F-008. The request is held open, so anything that appears before it
    // resolves can only have come from the optimistic update.
    let release: (() => void) | undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(
          envelope([makeNotification(20, { title: "Needs attention" })]),
        ),
      ),
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: 1 })),
      ),
      http.patch(`${API}/api/v1/notifications/:id/read`, async () => {
        await held;
        return HttpResponse.json(envelope({}));
      }),
    );

    renderBell();
    await openFeed();
    await userEvent.click(screen.getByRole("button", { name: "Mark as read" }));

    // The button only renders while the row is unread, so its disappearance
    // is the row having been marked read in the cache.
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Mark as read" }),
      ).not.toBeInTheDocument();
    });

    release?.();
  });

  it("rolls the row back when the server rejects the change", async () => {
    server.use(
      http.get(`${API}/api/v1/notifications`, () =>
        HttpResponse.json(
          envelope([makeNotification(20, { title: "Needs attention" })]),
        ),
      ),
      http.get(`${API}/api/v1/notifications/unread-count`, () =>
        HttpResponse.json(envelope({ count: 1 })),
      ),
      http.patch(`${API}/api/v1/notifications/:id/read`, () =>
        HttpResponse.json({ error: { message: "nope" } }, { status: 500 }),
      ),
    );

    renderBell();
    await openFeed();
    await userEvent.click(screen.getByRole("button", { name: "Mark as read" }));

    // Optimistically hidden, then restored by onError's snapshot.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Mark as read" }),
      ).toBeInTheDocument();
    });
  });
});
