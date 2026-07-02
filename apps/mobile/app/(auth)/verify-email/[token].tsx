import { useEffect, useRef, useState } from "react";
import { Link, useLocalSearchParams } from "expo-router";
import { useTranslation } from "react-i18next";
import { ActivityIndicator, Text } from "react-native";

import { verifyEmailApiV1AuthVerifyEmailPost } from "@forge/api-client";

import { AuthCard } from "../../../src/components/AuthCard";
import { Banner } from "../../../src/components/Banner";
import { client } from "../../../src/lib/api";
import { getErrorMessage } from "../../../src/lib/getErrorMessage";

type VerifyState = "verifying" | "success" | "error";

export default function VerifyEmailScreen() {
  const { t } = useTranslation();
  const { token } = useLocalSearchParams<{ token: string }>();
  const [state, setState] = useState<VerifyState>(
    token ? "verifying" : "error",
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Verification tokens are single-use; this guard keeps a remount (e.g. a
  // fast-refresh during development) from consuming the token twice and
  // reporting a still-valid link as expired.
  const requested = useRef(false);

  useEffect(() => {
    if (!token || requested.current) {
      return;
    }
    requested.current = true;
    void verifyEmailApiV1AuthVerifyEmailPost({
      client,
      body: { token },
      throwOnError: true,
    })
      .then(() => {
        setState("success");
      })
      .catch((error: unknown) => {
        setErrorMessage(
          getErrorMessage(error, "auth.verify_email.error_invalid_or_expired"),
        );
        setState("error");
      });
  }, [token]);

  if (state === "verifying") {
    return (
      <AuthCard title={t("auth.verify_email.title_verifying")}>
        <ActivityIndicator />
      </AuthCard>
    );
  }

  if (state === "success") {
    return (
      <AuthCard title={t("auth.verify_email.title_success")}>
        <Banner>{t("auth.verify_email.success_message")}</Banner>
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.verify_email.continue_to_login")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.verify_email.title_failed")}>
      <Banner variant="error">
        {errorMessage ?? t("auth.verify_email.error_default")}
      </Banner>
      <Text className="text-center text-sm text-slate-500">
        <Link href="/register">
          <Text className="text-sm text-slate-600 underline">
            {t("auth.verify_email.create_account")}
          </Text>
        </Link>{" "}
        {t("auth.verify_email.or")}{" "}
        <Link href="/login">
          <Text className="text-sm text-slate-600 underline">
            {t("auth.verify_email.login")}
          </Text>
        </Link>
      </Text>
    </AuthCard>
  );
}
