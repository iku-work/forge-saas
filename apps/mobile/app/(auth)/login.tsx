import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useRouter } from "expo-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Text, View } from "react-native";

import { loginSchema, type LoginInput } from "@forge/validation";

import { AuthCard } from "../../src/components/AuthCard";
import { Banner } from "../../src/components/Banner";
import { Button } from "../../src/components/Button";
import { FormField } from "../../src/components/FormField";
import { TwoFactorChallenge } from "../../src/components/TwoFactorChallenge";
import { login, TwoFactorRequiredError } from "../../src/lib/auth";
import { getErrorMessage } from "../../src/lib/getErrorMessage";

// No "Continue with Google/GitHub" buttons here yet: the backend's OAuth
// callback (backend/app/modules/auth/oauth.py) always returns a JSON
// envelope to whatever browser hit it, the same way it does for web. Web
// gets away with this because the call also sets the session cookie the SPA
// already shares with that browser; mobile's in-app browser session has no
// such shared channel back to this app's own fetch client, so there is
// nothing useful for a button here to do until the backend can redirect a
// mobile-initiated login to a deep link with tokens attached.
export default function LoginScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const [formError, setFormError] = useState<string | null>(null);
  const [tempToken, setTempToken] = useState<string | null>(null);

  const { control, handleSubmit, formState } = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = async (values: LoginInput): Promise<void> => {
    setFormError(null);
    try {
      await login(values.email, values.password);
      router.replace("/dashboard");
    } catch (error) {
      // Not a failure: the password was right and the account wants a
      // second factor. Swapping the form out is the whole fix -- this used
      // to fall through to setFormError, which rendered "Two-factor
      // authentication is required" as a dead end.
      if (error instanceof TwoFactorRequiredError) {
        setTempToken(error.tempToken);
        return;
      }
      setFormError(getErrorMessage(error, "auth.login.error_default"));
    }
  };

  if (tempToken) {
    return (
      <AuthCard
        title={t("auth.two_factor_challenge.title")}
        description={t("auth.two_factor_challenge.description")}
      >
        <TwoFactorChallenge
          tempToken={tempToken}
          onSuccess={() => {
            router.replace("/dashboard");
          }}
        />
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.login.title")}
      description={t("auth.login.description")}
      footer={
        <View className="gap-2">
          <View className="flex-row justify-center gap-1">
            <Link href="/forgot-password">
              <Text className="text-sm text-slate-600 underline">
                {t("auth.login.forgot_password")}
              </Text>
            </Link>
            <Text className="text-sm text-slate-400">·</Text>
            <Link href="/magic-link">
              <Text className="text-sm text-slate-600 underline">
                {t("auth.login.magic_link_cta")}
              </Text>
            </Link>
          </View>
          <Text className="text-center text-sm text-slate-500">
            {t("auth.login.no_account")}{" "}
            <Link href="/register">
              <Text className="text-sm text-slate-600 underline">
                {t("auth.login.signup_link")}
              </Text>
            </Link>
          </Text>
        </View>
      }
    >
      {formError ? <Banner variant="error">{formError}</Banner> : null}

      <View className="gap-4">
        <FormField
          control={control}
          name="email"
          label={t("auth.login.email_label")}
          autoComplete="email"
          keyboardType="email-address"
        />
        <FormField
          control={control}
          name="password"
          label={t("auth.login.password_label")}
          secureTextEntry
          autoComplete="current-password"
        />
        <Button
          label={
            formState.isSubmitting
              ? t("auth.login.submit_pending")
              : t("auth.login.submit")
          }
          hint={t("auth.login.submit_hint")}
          disabled={formState.isSubmitting}
          onPress={() => {
            void handleSubmit(onSubmit)();
          }}
        />
      </View>
    </AuthCard>
  );
}
