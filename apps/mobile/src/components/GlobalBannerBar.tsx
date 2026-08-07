import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Pressable, Text, View } from "react-native";

import { cn } from "@forge/utils";

import { useGlobalBanner, type GlobalBanner } from "../lib/config";

/**
 * T-263: the product-wide announcement, mirroring apps/web's GlobalBanner.
 *
 * Named `GlobalBannerBar` rather than `GlobalBanner` because this directory
 * already has a `Banner` (the inline form-level one) and because the type it
 * renders is called `GlobalBanner` -- a component and a type with one name in
 * one file reads fine until the first import that wants both.
 *
 * Mounted in the root layout, below ImpersonationBanner: an operator inside an
 * impersonation session must see *that* first. Rendered inline (pushing the
 * screen down) for the same reason ImpersonationBanner is -- an absolutely
 * positioned bar can be scrolled under or covered by a modal.
 *
 * The message is operator prose, rendered verbatim; only the chrome around it
 * is translated. See the web component for why.
 */

const STYLES: Record<
  GlobalBanner["type"],
  { container: string; text: string }
> = {
  info: { container: "bg-sky-100", text: "text-sky-950" },
  warning: { container: "bg-amber-100", text: "text-amber-950" },
  critical: { container: "bg-red-600", text: "text-white" },
};

export function GlobalBannerBar() {
  const { t } = useTranslation();
  const banner = useGlobalBanner();
  // Keyed on the message, not a boolean: an operator who changes the banner is
  // saying something new, which a previous dismissal cannot have covered.
  const [dismissedMessage, setDismissedMessage] = useState<string | null>(null);

  if (!banner || banner.message === dismissedMessage) {
    return null;
  }

  const style = STYLES[banner.type];

  return (
    <View
      accessibilityRole={banner.type === "critical" ? "alert" : "text"}
      className={cn("flex-row items-center gap-3 px-4 py-2", style.container)}
    >
      <Text className={cn("flex-1 text-sm", style.text)}>{banner.message}</Text>
      {banner.dismissible ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("common.banner.dismiss")}
          accessibilityHint={t("common.banner.dismiss_hint")}
          onPress={() => {
            setDismissedMessage(banner.message);
          }}
          className="rounded bg-black/10 px-2 py-1"
        >
          {/* The glyph carries no meaning for a screen reader -- the
              accessibilityLabel above is what it announces -- so it is
              deliberately a symbol rather than a seventh translated string,
              matching the X icon apps/web's GlobalBanner uses. */}
          <Text className={cn("text-base font-semibold", style.text)}>×</Text>
        </Pressable>
      ) : null}
    </View>
  );
}
