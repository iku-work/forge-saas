import { useTranslation } from "react-i18next";

interface ImpersonationBannerProps {
  email: string;
  onEnd: () => void;
}

// T-142: rendered above the whole app shell (AppRoot), never just AppLayout
// -- impersonation should be impossible to forget regardless of which route
// tree (user-facing or, later, _admin) is currently mounted. Deliberately
// has no dismiss/close control -- ending the session is the only way to
// make it go away.
export function ImpersonationBanner({
  email,
  onEnd,
}: ImpersonationBannerProps) {
  const { t } = useTranslation();

  return (
    <div
      role="alert"
      className="flex items-center justify-center gap-3 bg-warning px-4 py-2 text-center text-sm font-medium text-warning-foreground"
    >
      <span>{t("common.impersonation.banner", { email })}</span>
      <button
        type="button"
        onClick={onEnd}
        className="underline underline-offset-2 hover:no-underline"
      >
        {t("common.impersonation.end")}
      </button>
    </div>
  );
}
