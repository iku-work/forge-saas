/**
 * Expo push notification registration + handling (T-105). Mobile-only from
 * the start -- there's no web counterpart to reimplement-rather-than-share
 * here (lib/locale.ts's precedent) since browsers don't have Expo push
 * tokens at all.
 *
 * Foreground notifications never show the OS's native banner (the default
 * `handleNotification` response below suppresses it) -- a custom in-app
 * banner is shown instead via useNotificationBanner(), mirroring how the web
 * NotificationFeed renders in-app rather than relying on the browser's
 * native notification UI.
 */
import { useSyncExternalStore } from "react";
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { router, type Href } from "expo-router";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

import { registerPushTokenApiV1NotificationsPushTokensPost } from "@forge/api-client";

import { client } from "./api";

const LAST_REGISTERED_TOKEN_KEY = "forge_push_token";

type PushPlatform = "ios" | "android";

// Constants.expoConfig.extra is typed `{ [k: string]: any }` by
// @expo/config-types -- narrowed through this interface via `as` so reading
// `.eas?.projectId` doesn't trip no-unsafe-member-access.
interface EasExtra {
  eas?: { projectId?: string };
}

function getPlatform(): PushPlatform | null {
  return Platform.OS === "ios" || Platform.OS === "android"
    ? Platform.OS
    : null;
}

function getProjectId(): string | undefined {
  const extra = Constants.expoConfig?.extra as EasExtra | undefined;
  return extra?.eas?.projectId;
}

export async function registerPushToken(): Promise<void> {
  const platform = getPlatform();
  if (!platform) {
    return; // Expo push tokens only exist on iOS/Android
  }

  const current = await Notifications.getPermissionsAsync();
  const granted = current.granted
    ? true
    : (await Notifications.requestPermissionsAsync()).granted;
  if (!granted) {
    return; // user declined -- not an error
  }

  const projectId = getProjectId();
  const { data: token } = await Notifications.getExpoPushTokenAsync(
    projectId ? { projectId } : undefined,
  );

  const lastRegistered = await SecureStore.getItemAsync(
    LAST_REGISTERED_TOKEN_KEY,
  );
  if (lastRegistered === token) {
    return; // unchanged since the last successful registration -- skip
  }

  await registerPushTokenApiV1NotificationsPushTokensPost({
    client,
    body: { token, platform, device_label: Device.modelName },
    throwOnError: true,
  });
  await SecureStore.setItemAsync(LAST_REGISTERED_TOKEN_KEY, token);
}

interface BannerState {
  title: string;
  body: string;
  /**
   * The locale the backend rendered `title`/`body` in, taken from the
   * payload's `data.locale` (T-234). The device's own language can differ --
   * `User.locale` is what the push was written in, and it is deliberately
   * not overwritten by the system locale -- so anything this app renders
   * around those two strings follows this rather than i18next's current
   * language, or the banner ends up half in each. Null when the payload
   * predates T-234.
   */
  locale: string | null;
}

// Module-scoped store (not React Context), same pattern as lib/auth.ts's
// useSession -- the banner can be set from a listener registered once at
// mount, with no provider component required.
let bannerState: BannerState | null = null;
const bannerListeners = new Set<() => void>();

function setBannerState(next: BannerState | null): void {
  bannerState = next;
  for (const listener of bannerListeners) {
    listener();
  }
}

function subscribeBanner(listener: () => void): () => void {
  bannerListeners.add(listener);
  return () => {
    bannerListeners.delete(listener);
  };
}

function getBannerSnapshot(): BannerState | null {
  return bannerState;
}

export function useNotificationBanner(): BannerState | null {
  return useSyncExternalStore(subscribeBanner, getBannerSnapshot);
}

export function dismissNotificationBanner(): void {
  setBannerState(null);
}

function getStringField(
  data: Record<string, unknown>,
  field: string,
): string | null {
  const value = data[field];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function showInAppBanner(event: Notifications.Notification): void {
  const { title, body, data } = event.request.content;
  setBannerState({
    title: title ?? "",
    body: body ?? "",
    locale: getStringField(data, "locale"),
  });
}

function getActionUrl(data: Record<string, unknown>): string | null {
  return getStringField(data, "action_url");
}

function handleNotificationResponse(
  response: Notifications.NotificationResponse,
): void {
  const actionUrl = getActionUrl(response.notification.request.content.data);
  if (actionUrl) {
    // Backend-controlled and unknown to the typed-routes generator ahead of
    // time -- same escape hatch NotificationsScreen.tsx's own action link
    // uses for the identical field.
    router.push(actionUrl as Href);
  }
}

// Returns a cleanup function so the caller (apps/mobile/app/(app)/_layout.tsx)
// can tear listeners down on unmount -- mirrors every other
// addXListener/cleanup pair already in this codebase (onSessionExpired,
// useSystemLocaleSync).
export function setupNotificationHandlers(): () => void {
  Notifications.setNotificationHandler({
    handleNotification: () =>
      Promise.resolve({
        shouldShowAlert: false,
        shouldPlaySound: false,
        shouldSetBadge: false,
      }),
  });

  const received =
    Notifications.addNotificationReceivedListener(showInAppBanner);
  const responded = Notifications.addNotificationResponseReceivedListener(
    handleNotificationResponse,
  );
  const tokenRefreshed = Notifications.addPushTokenListener(() => {
    void registerPushToken();
  });

  return () => {
    received.remove();
    responded.remove();
    tokenRefreshed.remove();
  };
}
