import { useState } from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  useGlobalBanner,
  type GlobalBanner as GlobalBannerValue,
} from "@forge/hooks";

// T-263: the product-wide announcement an operator raises without a redeploy.
//
// Rendered above the app shell (see AppLayout/AdminLayout) rather than inside
// a page, so it survives navigation and is the first thing on the screen
// during an incident.
//
// Message text is *operator prose*, not a translation key: whoever writes it
// is writing one string for everyone, and the alternative -- a key the
// operator has to have added to six catalogs in advance -- is a banner that
// can only announce things somebody predicted. The surrounding chrome (the
// dismiss control's label, the severity announced to a screen reader) is
// translated normally.

const STYLES: Record<GlobalBannerValue["type"], string> = {
  info: "bg-sky-100 text-sky-950 dark:bg-sky-950 dark:text-sky-50",
  warning: "bg-amber-100 text-amber-950 dark:bg-amber-950 dark:text-amber-50",
  critical: "bg-red-600 text-white",
};

// `alert` interrupts a screen reader immediately; `status` waits for a pause.
// A critical banner is worth the interruption, the other two are not.
const ARIA_ROLE: Record<GlobalBannerValue["type"], "alert" | "status"> = {
  info: "status",
  warning: "status",
  critical: "alert",
};

export function GlobalBanner() {
  const { t } = useTranslation();
  const banner = useGlobalBanner();
  // Keyed on the message rather than a boolean: an operator who *changes* the
  // banner is saying something new, and a user who dismissed the previous one
  // has not dismissed this one.
  const [dismissedMessage, setDismissedMessage] = useState<string | null>(null);

  if (!banner || banner.message === dismissedMessage) {
    return null;
  }

  return (
    <div
      role={ARIA_ROLE[banner.type]}
      data-testid="global-banner"
      data-banner-type={banner.type}
      className={`flex items-center justify-center gap-3 px-4 py-2 text-sm ${STYLES[banner.type]}`}
    >
      <span className="sr-only">
        {t(`common.banner.severity_${banner.type}`)}
      </span>
      <p className="text-center">{banner.message}</p>
      {banner.dismissible ? (
        <button
          type="button"
          onClick={() => {
            setDismissedMessage(banner.message);
          }}
          aria-label={t("common.banner.dismiss")}
          className="shrink-0 rounded p-1.5 hover:bg-black/10"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}
