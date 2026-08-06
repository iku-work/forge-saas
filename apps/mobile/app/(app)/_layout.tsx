import { useEffect } from "react";
import { Redirect, Stack } from "expo-router";

import { NotificationBanner } from "../../src/components/NotificationBanner";
import { useSession } from "../../src/lib/auth";
import { useImpersonation } from "../../src/lib/impersonation";
import {
  registerPushToken,
  setupNotificationHandlers,
} from "../../src/lib/push";

export default function AppLayout() {
  const { user } = useSession();
  const impersonation = useImpersonation();

  useEffect(() => {
    if (!user) {
      return;
    }
    // T-261: never register this device's push token against the impersonated
    // user. Doing so would point their notifications at the superadmin's
    // phone -- and would keep pointing there after the session ended, since
    // the backend has no idea the registration was made under an
    // impersonation token. Handlers still run: pushes that arrive are the
    // ones this device was already subscribed to.
    if (!impersonation) {
      void registerPushToken();
    }
    return setupNotificationHandlers();
  }, [user, impersonation]);

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
