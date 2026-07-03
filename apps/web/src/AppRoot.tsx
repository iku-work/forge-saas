import { useEffect, useState } from "react";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { onSessionExpired } from "@forge/api-client";
import { useAuth } from "@forge/hooks";

import { applyProfileLocale } from "./i18n";
import { ImpersonationBanner } from "./layouts/ImpersonationBanner";
import type { AppRouter } from "./router";

interface AppRootProps {
  router: AppRouter;
  queryClient: QueryClient;
}

export function AppRoot({ router, queryClient }: AppRootProps) {
  const { user, isLoading, isImpersonating, endImpersonation } = useAuth();
  const { t } = useTranslation();
  const [localeReady, setLocaleReady] = useState(false);

  // useAuth() clears its own user state when a refresh-and-retry cycle
  // determines the session is over, but that alone won't move someone off
  // a currently-guarded page -- only the next navigation re-runs `_app`'s
  // beforeLoad. Force that navigation here instead of waiting for one.
  useEffect(
    () => onSessionExpired(() => void router.navigate({ to: "/login" })),
    [router],
  );

  // Detection order (T-069): a signed-in user's saved locale outranks the
  // browser-language guess apps/web/src/i18n.ts made before anyone was known
  // to be logged in. Held behind the same loading gate as auth itself so a
  // returning user whose profile locale differs from their browser's never
  // sees a flash of the wrong language.
  useEffect(() => {
    if (isLoading) {
      return;
    }
    void applyProfileLocale(user?.locale).finally(() => {
      setLocaleReady(true);
    });
  }, [isLoading, user]);

  return (
    <QueryClientProvider client={queryClient}>
      {/* T-142: rendered above both branches below so it's visible during
          the loading state too, not just once the router mounts -- an
          impersonation session should never have a moment where the banner
          isn't there. */}
      {isImpersonating && user ? (
        <ImpersonationBanner
          email={user.email}
          onEnd={() => {
            void endImpersonation();
          }}
        />
      ) : null}
      {isLoading || !localeReady ? (
        <div className="flex min-h-screen items-center justify-center">
          <span className="text-sm text-muted-foreground">
            {t("common.status.loading")}
          </span>
        </div>
      ) : (
        <RouterProvider router={router} context={{ auth: { user } }} />
      )}
    </QueryClientProvider>
  );
}
