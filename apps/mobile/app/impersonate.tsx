import { useEffect, useState } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import { ActivityIndicator, Text, View } from "react-native";

import { AuthCard } from "../src/components/AuthCard";
import { Banner } from "../src/components/Banner";
import { Button } from "../src/components/Button";
import { useCountdown } from "../src/components/ImpersonationBanner";
import { bootstrapSession } from "../src/lib/auth";
import {
  acceptHandoff,
  declineHandoff,
  previewHandoff,
  type HandoffPreview,
} from "../src/lib/impersonation";

/**
 * T-261: where `forge://impersonate?token=...&user_email=...` lands.
 *
 * Nothing is adopted until Confirm is pressed. The screen previews the handoff
 * first (who, why, how long is left) because an operator scanning a QR code
 * off a screen has no other way to check they are about to become the right
 * person for the right reason -- and because a link that arrived unexpectedly
 * should be declinable, which is what Cancel does: it burns the token
 * server-side rather than leaving it live for the rest of its two minutes.
 *
 * `user_email` from the query string is shown while the preview loads and is
 * informational only -- the identity that actually takes effect is whatever
 * the exchange hands back, which is the impersonation token's own subject.
 */
export default function ImpersonateConfirmScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { token, user_email: linkEmail } = useLocalSearchParams<{
    token?: string;
    user_email?: string;
  }>();
  const [preview, setPreview] = useState<HandoffPreview | null>(null);
  const [failure, setFailure] = useState<"link" | "exchange" | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  // A link with no token at all is decided during render, not by an effect
  // that sets state on mount -- there is nothing asynchronous about it.
  const error =
    !token || failure === "link"
      ? t("common.impersonation.confirm_invalid")
      : failure === "exchange"
        ? t("common.impersonation.confirm_failed")
        : null;

  useEffect(() => {
    if (!token) {
      return;
    }
    let isMounted = true;
    void previewHandoff(token)
      .then((result) => {
        if (isMounted) setPreview(result);
      })
      .catch(() => {
        if (isMounted) setFailure("link");
      });
    return () => {
      isMounted = false;
    };
  }, [token]);

  const handleConfirm = (): void => {
    if (!token) return;
    setIsBusy(true);
    void acceptHandoff(token)
      .then(async () => {
        // Resolves useSession() from the token just stored, so the (app)
        // group's guard sees a signed-in user instead of bouncing straight
        // back to /login.
        await bootstrapSession();
        router.replace("/dashboard");
      })
      .catch(() => {
        setFailure("exchange");
        setIsBusy(false);
      });
  };

  const handleDecline = (): void => {
    setIsBusy(true);
    // The link dies either way -- a failed cancel call leaves it to its
    // two-minute expiry, which is not a reason to keep the operator here.
    void (
      token ? declineHandoff(token).catch(() => undefined) : Promise.resolve()
    ).finally(() => {
      router.replace("/login");
    });
  };

  if (error) {
    return (
      <AuthCard title={t("common.impersonation.confirm_title")}>
        <Banner variant="error">{error}</Banner>
        <Button
          label={t("common.impersonation.confirm_decline")}
          hint={t("common.impersonation.confirm_decline_hint")}
          variant="outline"
          onPress={() => {
            router.replace("/login");
          }}
        />
      </AuthCard>
    );
  }

  if (!preview) {
    return (
      <AuthCard title={t("common.impersonation.confirm_title")}>
        <Text className="text-sm text-slate-600">
          {t("common.impersonation.confirm_user", { email: linkEmail ?? "" })}
        </Text>
        <Text className="text-sm text-slate-600">
          {t("common.impersonation.confirm_loading")}
        </Text>
        <ActivityIndicator />
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("common.impersonation.confirm_title")}>
      <Text className="text-sm text-slate-600">
        {t("common.impersonation.confirm_intro")}
      </Text>
      <View className="gap-1 rounded-md border border-slate-200 bg-slate-50 p-3">
        <Text className="text-sm font-semibold text-slate-900">
          {t("common.impersonation.confirm_user", {
            email: preview.user_email,
          })}
        </Text>
        <Text className="text-sm text-slate-700">
          {preview.reason
            ? t("common.impersonation.confirm_reason", {
                reason: preview.reason,
              })
            : t("common.impersonation.confirm_no_reason")}
        </Text>
        <SessionRemaining
          deadline={new Date(preview.session_expires_at).getTime()}
        />
      </View>
      <Button
        label={t("common.impersonation.confirm_accept")}
        hint={t("common.impersonation.confirm_accept_hint")}
        disabled={isBusy}
        onPress={handleConfirm}
      />
      <Button
        label={t("common.impersonation.confirm_decline")}
        hint={t("common.impersonation.confirm_decline_hint")}
        variant="outline"
        disabled={isBusy}
        onPress={handleDecline}
      />
    </AuthCard>
  );
}

/** Split out so useCountdown mounts with a deadline in hand -- see its
 * docstring for why it doesn't take a nullable one. */
function SessionRemaining({ deadline }: { deadline: number }) {
  const { t } = useTranslation();
  const remaining = useCountdown(deadline);

  return (
    <Text className="text-sm text-slate-700">
      {t("common.impersonation.confirm_remaining", { remaining })}
    </Text>
  );
}
