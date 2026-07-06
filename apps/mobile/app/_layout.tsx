import "../global.css";

import { useEffect, useState } from "react";
import * as Sentry from "@sentry/react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Slot, useRouter } from "expo-router";
import { ActivityIndicator, View } from "react-native";

import { onSessionExpired } from "../src/lib/api";
import { bootstrapSession, getSessionSnapshot, useSession } from "../src/lib/auth";
import {
  applyProfileLocale,
  initI18n,
  useSystemLocaleSync,
} from "../src/lib/i18n";
import { initSentry, setSentryUser } from "../src/lib/sentry";

// Module scope, same as apps/web/src/main.tsx's initSentry() call -- must
// run before anything else in the tree can throw.
initSentry();

const queryClient = new QueryClient();

function RootLayout() {
  const [isCheckingSession, setIsCheckingSession] = useState(true);
  const router = useRouter();
  const { user } = useSession();

  // expo-localization's useLocales() only re-renders this component when the
  // OS language actually changes -- mounted unconditionally so the app keeps
  // following a live device-settings change both before and after sign-in.
  useSystemLocaleSync();

  useEffect(() => {
    let isMounted = true;
    void (async () => {
      // Resources are bundled, so this resolves on the next microtask --
      // still awaited so the very first render already has the right
      // language instead of a flash of the previous one.
      await initI18n();
      const hadStoredSession = await bootstrapSession().catch(() => false);
      if (hadStoredSession) {
        // A signed-in user's saved locale outranks the device-language guess
        // initI18n() made before anyone was known to be logged in.
        await applyProfileLocale(getSessionSnapshot().user?.locale).catch(
          () => undefined,
        );
      }
    })().finally(() => {
      if (isMounted) setIsCheckingSession(false);
    });
    return () => {
      isMounted = false;
    };
  }, []);

  // Fires once a refresh-and-retry cycle (api.ts) has confirmed the refresh
  // token itself is gone, from anywhere in the app -- not just from a screen
  // that happens to be reading useSession().
  useEffect(() => {
    return onSessionExpired(() => {
      router.replace("/login");
    });
  }, [router]);

  // T-201: id only, never email or name -- kept in sync with whichever
  // account the rest of this component tree is currently rendering for.
  useEffect(() => {
    setSentryUser(user ? { id: user.id } : null);
  }, [user]);

  if (isCheckingSession) {
    return (
      <View className="flex-1 items-center justify-center bg-white">
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <Slot />
    </QueryClientProvider>
  );
}

// T-201: reports unhandled render errors + adds native/JS performance
// instrumentation around the whole tree, same role as apps/web's
// Sentry.ErrorBoundary wrapper -- a no-op if initSentry() above didn't
// initialize the SDK (no DSN configured).
export default Sentry.wrap(RootLayout);
