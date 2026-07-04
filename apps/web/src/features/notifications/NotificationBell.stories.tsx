import { http, HttpResponse } from "msw";
import { userEvent, waitFor, within } from "storybook/test";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, makeNotification } from "../../test/appShellFixtures";
import { NotificationBell } from "./NotificationBell";

// No access token is set in this Storybook environment, so
// subscribeToNotificationStream's fetch (apps/web/src/lib/sse.ts) throws
// immediately and is swallowed by its own reconnect-scheduling `catch` --
// the bell renders exactly as it does for these three states without a
// live stream, no module mocking required (T-150).
const API = getApiBaseUrl();

const meta = {
  title: "Notifications/NotificationBell",
  component: NotificationBell,
} satisfies Meta<typeof NotificationBell>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get(`${API}/api/v1/notifications`, () => HttpResponse.json(envelope([]))),
        http.get(`${API}/api/v1/notifications/unread-count`, () =>
          HttpResponse.json(envelope({ count: 0 })),
        ),
      ],
    },
  },
};

export const WithUnread: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get(`${API}/api/v1/notifications`, () =>
          HttpResponse.json(
            envelope([
              makeNotification(1, { title: "Jordan Lee joined Acme Inc" }),
              makeNotification(2, {
                title: "Payment failed",
                body: "Your card was declined.",
              }),
            ]),
          ),
        ),
        http.get(`${API}/api/v1/notifications/unread-count`, () =>
          HttpResponse.json(envelope({ count: 2 })),
        ),
      ],
    },
  },
};

export const DropdownOpen: Story = {
  ...WithUnread,
  play: async ({ canvasElement }) => {
    const trigger = await within(canvasElement).findByRole("button", {
      name: /notifications/i,
    });
    await userEvent.click(trigger);
    // PopoverContent renders into a body-level portal, outside canvasElement.
    await waitFor(() =>
      within(document.body).getByText("Jordan Lee joined Acme Inc"),
    );
  },
};
