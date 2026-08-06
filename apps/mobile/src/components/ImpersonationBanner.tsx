import { useEffect, useState } from "react";
import { useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import { Pressable, Text, View } from "react-native";

import {
  endImpersonation,
  useImpersonation,
  type ImpersonationState,
} from "../lib/impersonation";

/**
 * T-261: the "you are not yourself right now" banner.
 *
 * Mounted in the root layout rather than the (app) group's, so it is on every
 * screen an impersonated session can reach -- including the ones outside the
 * authenticated group -- and, unlike NotificationBanner next door, it has no
 * dismiss control and no auto-dismiss timer. The only thing that removes it is
 * ending the session.
 *
 * Rendered inline (pushing the screen down) rather than as an overlay: an
 * absolutely-positioned banner can be scrolled under or hidden behind a modal,
 * and "the operator forgot they were impersonating" is exactly the failure
 * this is here to prevent.
 */
export function ImpersonationBanner() {
  const session = useImpersonation();

  // The visible half is a separate component so it mounts *with* a deadline
  // already known -- see useCountdown below for why that matters.
  return session ? <ActiveImpersonationBanner session={session} /> : null;
}

function ActiveImpersonationBanner({
  session,
}: {
  session: ImpersonationState;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const remaining = useCountdown(session.expiresAt);

  return (
    <View
      accessibilityRole="alert"
      className="flex-row items-center justify-center gap-3 bg-red-600 px-4 pb-2 pt-12"
    >
      <Text className="flex-1 text-sm font-semibold text-white">
        {t("common.impersonation.mobile_banner", {
          email: session.email,
          remaining,
        })}
      </Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("common.impersonation.end")}
        accessibilityHint={t("common.impersonation.end_hint")}
        onPress={() => {
          void endImpersonation()
            .finally(() => {
              // Back to sign-in, not to the superadmin's own session: mobile
              // holds one session per install, so there is nothing underneath
              // this one to fall back to (web falls back to the superadmin's
              // refresh cookie, which mobile has no equivalent of).
              router.replace("/login");
            })
            // endImpersonation() wipes the device before it rethrows, so a
            // failed server call has already done the part that matters here;
            // leaving it unhandled would only surface as an unhandled
            // rejection on a screen that is already gone.
            .catch(() => undefined);
        }}
        className="rounded bg-white/20 px-3 py-1"
      >
        <Text className="text-sm font-semibold text-white">
          {t("common.impersonation.end")}
        </Text>
      </Pressable>
    </View>
  );
}

// Module scope, not inside the hook: reading the clock is impure, and render
// has to stay pure (react-hooks/purity). Every read below happens either in a
// lazy initializer or in a timer callback, never while rendering -- same shape
// as PendingInvitationsCard's isExpired() on web.
function formatRemaining(deadline: number): string {
  const total = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/**
 * mm:ss until `deadline` (epoch ms), floored at 00:00, ticking once a second.
 *
 * Each tick re-reads the clock rather than subtracting one from the last
 * value: a backgrounded app stops getting timer callbacks, and a counter that
 * decremented per fire would come back claiming minutes that have already
 * passed.
 *
 * `deadline` is required, not nullable, so the first rendered value is already
 * correct -- callers mount the component that uses this only once they have a
 * deadline (ActiveImpersonationBanner above, SessionRemaining in
 * app/impersonate.tsx). A nullable version would have to catch up with a
 * setState inside an effect, which is both a lint error and a visible flash of
 * "00:00".
 */
export function useCountdown(deadline: number): string {
  const [label, setLabel] = useState(() => formatRemaining(deadline));

  useEffect(() => {
    const timer = setInterval(() => {
      setLabel(formatRemaining(deadline));
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [deadline]);

  return label;
}
