import { useEffect } from "react";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { onSessionExpired } from "@forge/api-client";
import { useAuth } from "@forge/hooks";

import { detectPreferredLocale } from "./i18n";
import { AppErrorBoundary } from "./layouts/AppErrorBoundary";
import { ImpersonationBanner } from "./layouts/ImpersonationBanner";
import { localeOf, withLocale } from "./lib/locale-url";
import { setSentryUser } from "./lib/sentry";
import type { AppRouter } from "./router";

interface AppRootProps {
  router: AppRouter;
  queryClient: QueryClient;
}

// T-233: AppRoot sits *above* <RouterProvider>, so it can't use
// useRouteLocale() -- it reads the locale straight off the router's current
// location instead, falling back to the ordinary resolution order for the
// (only theoretically reachable) case where the router hasn't landed on a
// locale-prefixed URL yet.
function currentLocaleHref(router: AppRouter, path: string): string {
  const locale =
    localeOf(router.state.location.href) ?? detectPreferredLocale();
  return withLocale(path, locale);
}

export function AppRoot({ router, queryClient }: AppRootProps) {
  const { user, isLoading, isImpersonating, endImpersonation } = useAuth();
  const { t } = useTranslation();

  // useAuth() clears its own user state when a refresh-and-retry cycle
  // determines the session is over, but that alone won't move someone off
  // a currently-guarded page -- only the next navigation re-runs `_app`'s
  // beforeLoad. Force that navigation here instead of waiting for one.
  useEffect(
    () =>
      onSessionExpired(() => {
        void router.navigate({ href: currentLocaleHref(router, "/login") });
      }),
    [router],
  );

  // T-233 removed the applyProfileLocale() reconciliation that used to live
  // here: the URL now carries the locale, so a signed-in user's saved
  // preference is applied by routes/index.tsx's redirect (which resolves it
  // from `context.auth.user` -- see lib/locale-url.ts's
  // resolvePreferredLocale) and then by routes/$locale.tsx, rather than by
  // mutating i18next behind the router's back. The RouterProvider below is
  // already gated on `isLoading`, so `user` is known before that redirect
  // runs and there's still no flash of the wrong language.

  // T-201: id only, never email or name -- kept in sync with whichever
  // account (real or impersonated) the rest of this component tree is
  // currently rendering for.
  useEffect(() => {
    setSentryUser(user ? { id: user.id } : null);
  }, [user]);

  return (
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        {/* T-142: rendered above both branches below so it's visible during
            the loading state too, not just once the router mounts -- an
            impersonation session should never have a moment where the banner
            isn't there. */}
        {isImpersonating && user ? (
          <ImpersonationBanner
            email={user.email}
            onEnd={() => {
              // T-143: ending impersonation returns the superadmin to the
              // admin panel they started it from, rather than leaving them on
              // whatever page of the impersonated user's app they happened to
              // be viewing.
              void endImpersonation().then(() =>
                router.navigate({ href: currentLocaleHref(router, "/admin") }),
              );
            }}
          />
        ) : null}
        {isLoading ? (
          <div className="flex min-h-screen items-center justify-center">
            <span className="text-sm text-muted-foreground">
              {t("common.status.loading")}
            </span>
          </div>
        ) : (
          <RouterProvider router={router} context={{ auth: { user } }} />
        )}
      </QueryClientProvider>
    </AppErrorBoundary>
  );
}
