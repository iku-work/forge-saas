import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

// Reached when a route guard (e.g. a disabled module's settings page, see
// _app.settings.billing.tsx) throws notFound() -- without this, TanStack
// Router falls back to its own generic `<p>Not Found</p>` and a console
// warning on every hit.
export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-2 p-6 text-center">
      <p className="text-lg font-semibold">{t("common.not_found.title")}</p>
      <Link to="/" className="text-sm underline underline-offset-4">
        {t("common.not_found.back")}
      </Link>
    </div>
  );
}
