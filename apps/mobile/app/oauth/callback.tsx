import { useEffect, useRef } from "react";
import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import { ActivityIndicator, Text } from "react-native";

import { AuthCard } from "../../src/components/AuthCard";
import { Banner } from "../../src/components/Banner";
import { setStoredTokens } from "../../src/lib/api";

// backend/app/modules/auth/oauth.py's callback endpoint does not redirect
// here yet -- it returns a JSON envelope straight to whatever browser hit
// it, same as it does for web (see app/(auth)/login.tsx's comment on why
// there are no OAuth buttons there yet). This route exists so the
// `forge://oauth/callback` deep link itself resolves to a screen, and so it
// is ready to finish the job the moment the backend learns to redirect a
// mobile-initiated login here with `access_token`/`refresh_token` attached.
export default function OAuthCallbackScreen() {
  const { t } = useTranslation();
  const { access_token: accessToken, refresh_token: refreshToken } =
    useLocalSearchParams<{ access_token?: string; refresh_token?: string }>();
  const router = useRouter();
  const hasError = !accessToken || !refreshToken;
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current || !accessToken || !refreshToken) return;
    handled.current = true;
    void setStoredTokens(accessToken, refreshToken).then(() => {
      router.replace("/dashboard");
    });
  }, [accessToken, refreshToken, router]);

  if (hasError) {
    return (
      <AuthCard title={t("auth.oauth_callback.title_failed")}>
        <Banner variant="error">
          {t("auth.oauth_callback.error_message")}
        </Banner>
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.oauth_callback.back_to_login")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.oauth_callback.title_pending")}>
      <ActivityIndicator />
    </AuthCard>
  );
}
