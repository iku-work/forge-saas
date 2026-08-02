import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Pressable, Text } from "react-native";

import { dismissNotificationBanner, useNotificationBanner } from "../lib/push";

const AUTO_DISMISS_MS = 4000;

export function NotificationBanner() {
  const { t } = useTranslation();
  const banner = useNotificationBanner();

  useEffect(() => {
    if (!banner) {
      return;
    }
    const timer = setTimeout(dismissNotificationBanner, AUTO_DISMISS_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [banner]);

  if (!banner) {
    return null;
  }

  return (
    <Pressable
      accessibilityRole="alert"
      accessibilityLabel={banner.title || banner.body}
      // T-234: the banner's own chrome follows the language the push was
      // written in (banner.locale), not whatever the device is set to --
      // otherwise a Finnish push read by an English-locale device announces
      // itself half in each.
      accessibilityHint={t("notifications.banner.dismiss_hint", {
        ...(banner.locale ? { lng: banner.locale } : {}),
      })}
      onPress={dismissNotificationBanner}
      className="absolute left-4 right-4 top-12 z-50 rounded-lg bg-slate-900 p-4 shadow-lg"
    >
      <Text className="text-sm font-semibold text-white">{banner.title}</Text>
      <Text className="mt-1 text-sm text-slate-200">{banner.body}</Text>
    </Pressable>
  );
}
