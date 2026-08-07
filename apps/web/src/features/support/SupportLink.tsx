import { LifeBuoy } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useSupportContext, useSupportSettings } from "@forge/hooks";
import { supportMailtoUrl } from "@forge/utils";

// T-262: the seam between this template and whichever support tool a product
// picks. When SUPPORT_TOOL is anything but "none", clicking "Contact support"
// dispatches this event with the user's SupportContext as `detail`; a
// product-side listener (the documented SupportWidget wrapper -- see
// docs/runbooks/support-tool-integration.md) opens its vendor's widget and
// calls preventDefault() to say "handled". If nothing listens, or the widget
// script was blocked, the event goes unhandled and the link navigates to
// SUPPORT_URL as it normally would -- so support is never a dead button.
export const SUPPORT_OPEN_EVENT = "forge:support-open";

export function SupportLink() {
  const { t } = useTranslation();
  const settings = useSupportSettings();
  const context = useSupportContext();

  // Empty url = /config hasn't answered yet. Rendering nothing beats
  // rendering a link whose destination we'd have to guess.
  if (!settings.url) {
    return null;
  }

  // Only a mailto: is prefilled; see supportMailtoUrl. A widget tool's
  // fallback gets the same treatment, since the fallback is exactly the case
  // where the widget never got the context.
  const href = supportMailtoUrl(settings.url, context, {
    subject: t("common.support.subject"),
  });
  const isExternal = href.startsWith("http");

  return (
    <a
      href={href}
      title={t("common.support.contact_hint")}
      {...(isExternal
        ? { target: "_blank", rel: "noopener noreferrer" }
        : // mailto: must stay in this tab -- target="_blank" on one leaves
          // an orphaned blank tab behind in most browsers.
          {})}
      onClick={(event) => {
        if (settings.tool === "none") {
          return;
        }
        const handled = !window.dispatchEvent(
          new CustomEvent(SUPPORT_OPEN_EVENT, {
            detail: context,
            cancelable: true,
          }),
        );
        if (handled) {
          event.preventDefault();
        }
      }}
      className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      data-testid="support-link"
    >
      <LifeBuoy className="size-4" aria-hidden="true" />
      {t("common.support.contact")}
    </a>
  );
}
