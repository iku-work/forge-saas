import { useEffect } from "react";
import { Redirect, Stack } from "expo-router";

import { NotificationBanner } from "../../src/components/NotificationBanner";
import { useSession } from "../../src/lib/auth";
import {
  registerPushToken,
  setupNotificationHandlers,
} from "../../src/lib/push";

export default function AppLayout() {
  const { user } = useSession();

  useEffect(() => {
    if (!user) {
      return;
    }
    void registerPushToken();
    return setupNotificationHandlers();
  }, [user]);

  if (!user) {
    return <Redirect href="/login" />;
  }
  return (
    <>
      <Stack screenOptions={{ headerShown: false }} />
      <NotificationBanner />
    </>
  );
}
