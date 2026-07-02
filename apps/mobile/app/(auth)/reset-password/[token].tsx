import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useLocalSearchParams } from "expo-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Text } from "react-native";

import { resetPasswordApiV1AuthResetPasswordPost } from "@forge/api-client";
import {
  passwordResetSchema,
  type PasswordResetInput,
} from "@forge/validation";

import { AuthCard } from "../../../src/components/AuthCard";
import { Banner } from "../../../src/components/Banner";
import { Button } from "../../../src/components/Button";
import { FormField } from "../../../src/components/FormField";
import { client } from "../../../src/lib/api";
import { getErrorMessage } from "../../../src/lib/getErrorMessage";

export default function ResetPasswordScreen() {
  const { t } = useTranslation();
  const { token } = useLocalSearchParams<{ token: string }>();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const { control, handleSubmit, formState } = useForm<PasswordResetInput>({
    resolver: zodResolver(passwordResetSchema),
    defaultValues: { token, newPassword: "" },
  });

  if (!token) {
    return (
      <AuthCard title={t("auth.reset_password.invalid_title")}>
        <Banner variant="error">
          {t("auth.reset_password.invalid_message")}
        </Banner>
        <Link href="/forgot-password">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.reset_password.request_new")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  const onSubmit = async (values: PasswordResetInput): Promise<void> => {
    setFormError(null);
    try {
      await resetPasswordApiV1AuthResetPasswordPost({
        client,
        body: { token: values.token, new_password: values.newPassword },
        throwOnError: true,
      });
      setSubmitted(true);
    } catch (error) {
      setFormError(
        getErrorMessage(error, "auth.reset_password.error_invalid_or_expired"),
      );
    }
  };

  if (submitted) {
    return (
      <AuthCard title={t("auth.reset_password.success_title")}>
        <Banner>{t("auth.reset_password.success_message")}</Banner>
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.reset_password.continue_to_login")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.reset_password.title")}>
      {formError ? <Banner variant="error">{formError}</Banner> : null}

      <FormField
        control={control}
        name="newPassword"
        label={t("auth.reset_password.new_password_label")}
        hint={t("auth.reset_password.password_helper")}
        secureTextEntry
        autoComplete="new-password"
      />
      <Button
        label={
          formState.isSubmitting
            ? t("auth.reset_password.submit_pending")
            : t("auth.reset_password.submit")
        }
        hint={t("auth.reset_password.submit_hint")}
        disabled={formState.isSubmitting}
        onPress={() => {
          void handleSubmit(onSubmit)();
        }}
      />
    </AuthCard>
  );
}
