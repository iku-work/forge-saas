import * as Sentry from "@sentry/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

function ErrorFallback() {
  const { t } = useTranslation();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 text-center">
      <p className="text-sm text-muted-foreground">{t("common.error_boundary.title")}</p>
      <button
        type="button"
        onClick={() => {
          window.location.reload();
        }}
        className="text-sm font-medium underline underline-offset-4"
      >
        {t("common.error_boundary.reload")}
      </button>
    </div>
  );
}

// T-201: wraps the whole app (AppRoot) so a render error anywhere shows this
// branded page instead of a white screen -- Sentry.ErrorBoundary reports the
// error itself, same as the backend/mobile catch-all handlers.
export function AppErrorBoundary({ children }: { children: ReactNode }) {
  return <Sentry.ErrorBoundary fallback={<ErrorFallback />}>{children}</Sentry.ErrorBoundary>;
}
