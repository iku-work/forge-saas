import { beforeEach, describe, expect, it, vi } from "vitest";

// react-native is aliased to react-native-web in vitest.config.ts, where
// Platform.OS resolves to "web" -- overridden here so getPlatform() takes
// the iOS branch, same as a real device would.
vi.mock("react-native", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-native")>();
  return { ...actual, Platform: { ...actual.Platform, OS: "ios" } };
});

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => secureStore);

const notifications = vi.hoisted(() => ({
  getPermissionsAsync: vi.fn(),
  requestPermissionsAsync: vi.fn(),
  getExpoPushTokenAsync: vi.fn(),
  setNotificationHandler: vi.fn(),
  addNotificationReceivedListener: vi.fn().mockReturnValue({ remove: vi.fn() }),
  addNotificationResponseReceivedListener: vi
    .fn()
    .mockReturnValue({ remove: vi.fn() }),
  addPushTokenListener: vi.fn().mockReturnValue({ remove: vi.fn() }),
}));
vi.mock("expo-notifications", () => notifications);

vi.mock("expo-device", () => ({ modelName: "Pixel 9" }));
vi.mock("expo-constants", () => ({
  default: { expoConfig: { extra: { eas: { projectId: "project-123" } } } },
}));

const mocks = vi.hoisted(() => ({
  registerPushTokenApiV1NotificationsPushTokensPost: vi.fn(),
  router: { push: vi.fn() },
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return {
    ...actual,
    registerPushTokenApiV1NotificationsPushTokensPost:
      mocks.registerPushTokenApiV1NotificationsPushTokensPost,
  };
});
vi.mock("expo-router", () => ({ router: mocks.router }));

const { registerPushToken, setupNotificationHandlers } = await import("./push");

const GRANTED = {
  granted: true,
  status: "granted",
  expires: "never",
  canAskAgain: true,
} as const;
const DENIED = {
  granted: false,
  status: "denied",
  expires: "never",
  canAskAgain: true,
} as const;

describe("lib/push", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    secureStore.getItemAsync.mockResolvedValue(null);
    notifications.getPermissionsAsync.mockResolvedValue(GRANTED);
    notifications.getExpoPushTokenAsync.mockResolvedValue({
      type: "expo",
      data: "ExponentPushToken[abc]",
    });
    mocks.registerPushTokenApiV1NotificationsPushTokensPost.mockResolvedValue({
      data: {
        data: { id: "token-1", platform: "ios", device_label: "Pixel 9" },
        error: null,
      },
    });
  });

  describe("registerPushToken", () => {
    it("retrieves the Expo push token and POSTs it with the device's platform + label", async () => {
      await registerPushToken();

      expect(
        mocks.registerPushTokenApiV1NotificationsPushTokensPost,
      ).toHaveBeenCalledWith(
        expect.objectContaining({
          body: {
            token: "ExponentPushToken[abc]",
            platform: "ios",
            device_label: "Pixel 9",
          },
        }),
      );
      expect(secureStore.setItemAsync).toHaveBeenCalledWith(
        "forge_push_token",
        "ExponentPushToken[abc]",
      );
    });

    it("does not register and does not throw when permission is denied", async () => {
      notifications.getPermissionsAsync.mockResolvedValue(DENIED);
      notifications.requestPermissionsAsync.mockResolvedValue(DENIED);

      await expect(registerPushToken()).resolves.toBeUndefined();

      expect(
        mocks.registerPushTokenApiV1NotificationsPushTokensPost,
      ).not.toHaveBeenCalled();
    });

    it("requests permission when not already granted", async () => {
      notifications.getPermissionsAsync.mockResolvedValue(DENIED);
      notifications.requestPermissionsAsync.mockResolvedValue(GRANTED);

      await registerPushToken();

      expect(notifications.requestPermissionsAsync).toHaveBeenCalled();
      expect(
        mocks.registerPushTokenApiV1NotificationsPushTokensPost,
      ).toHaveBeenCalled();
    });

    it("skips re-registration when the token is unchanged since last time", async () => {
      secureStore.getItemAsync.mockResolvedValue("ExponentPushToken[abc]");

      await registerPushToken();

      expect(
        mocks.registerPushTokenApiV1NotificationsPushTokensPost,
      ).not.toHaveBeenCalled();
    });
  });

  describe("setupNotificationHandlers", () => {
    interface FakeNotificationResponse {
      notification: {
        date: number;
        request: {
          identifier: string;
          content: {
            title: string | null;
            subtitle: string | null;
            body: string | null;
            data: Record<string, unknown>;
            sound: string | null;
          };
          trigger: { type: string };
        };
      };
      actionIdentifier: string;
    }

    let respond: (response: FakeNotificationResponse) => void;
    let onTokenRefresh: (token: { type: string; data: string }) => void;

    beforeEach(() => {
      notifications.addNotificationResponseReceivedListener.mockImplementation(
        (listener: (response: FakeNotificationResponse) => void) => {
          respond = listener;
          return { remove: vi.fn() };
        },
      );
      notifications.addPushTokenListener.mockImplementation(
        (listener: (token: { type: string; data: string }) => void) => {
          onTokenRefresh = listener;
          return { remove: vi.fn() };
        },
      );
    });

    it("navigates to the notification's action_url when the user taps it", () => {
      setupNotificationHandlers();

      respond({
        notification: {
          date: Date.now(),
          request: {
            identifier: "notif-1",
            content: {
              title: "Payment failed",
              subtitle: null,
              body: "Update your card",
              data: { action_url: "/settings" },
              sound: null,
            },
            trigger: { type: "push" },
          },
        },
        actionIdentifier: "expo.modules.notifications.actions.DEFAULT",
      });

      expect(mocks.router.push).toHaveBeenCalledWith("/settings");
    });

    it("does not navigate when the notification has no action_url", () => {
      setupNotificationHandlers();

      respond({
        notification: {
          date: Date.now(),
          request: {
            identifier: "notif-2",
            content: {
              title: "Hello",
              subtitle: null,
              body: "Just FYI",
              data: {},
              sound: null,
            },
            trigger: { type: "push" },
          },
        },
        actionIdentifier: "expo.modules.notifications.actions.DEFAULT",
      });

      expect(mocks.router.push).not.toHaveBeenCalled();
    });

    it("re-registers with the refreshed token when Expo rolls it", async () => {
      setupNotificationHandlers();

      notifications.getExpoPushTokenAsync.mockResolvedValue({
        type: "expo",
        data: "ExponentPushToken[refreshed]",
      });
      onTokenRefresh({ type: "ios", data: "native-device-token" });

      await vi.waitFor(() => {
        expect(
          mocks.registerPushTokenApiV1NotificationsPushTokensPost,
        ).toHaveBeenCalledWith(
          expect.objectContaining({
            body: {
              token: "ExponentPushToken[refreshed]",
              platform: "ios",
              device_label: "Pixel 9",
            },
          }),
        );
      });
    });
  });
});
