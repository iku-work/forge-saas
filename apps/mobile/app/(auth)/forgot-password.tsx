import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "expo-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Text } from "react-native";

import { forgotPasswordApiV1AuthForgotPasswordPost } from "@forge/api-client";
import {
  forgotPasswordSchema,
  type ForgotPasswordInput,
} from "@forge/validation";

import { AuthCard } from "../../src/components/AuthCard";
import { Banner } from "../../src/components/Banner";
import { Button } from "../../src/components/Button";
import { FormField } from "../../src/components/FormField";
import { client } from "../../src/lib/api";
import { getErrorMessage } from "../../src/lib/getErrorMessage";
import { useCaptcha } from "../../src/lib/useCaptcha";

export default function ForgotPasswordScreen() {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const captcha = useCaptcha();

  const { control, handleSubmit, formState } = useForm<ForgotPasswordInput>({
    resolver: zodResolver(forgotPasswordSchema),
    defaultValues: { email: "" },
  });

  const onSubmit = async (values: ForgotPasswordInput): Promise<void> => {
    setFormError(null);
    try {
      await forgotPasswordApiV1AuthForgotPasswordPost({
        client,
        body: captcha.token
          ? { ...values, captcha_token: captcha.token }
          : values,
        throwOnError: true,
      });
      setSubmitted(true);
    } catch (error) {
      setFormError(
        getErrorMessage(error, "auth.forgot_password.error_default"),
      );
      captcha.reset();
    }
  };

  if (submitted) {
    return (
      <AuthCard title={t("auth.forgot_password.success_title")}>
        <Banner>{t("auth.forgot_password.success_message")}</Banner>
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.forgot_password.back_to_login")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.forgot_password.title")}
      description={t("auth.forgot_password.description")}
      footer={
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.forgot_password.back_to_login")}
          </Text>
        </Link>
      }
    >
      {formError ? <Banner variant="error">{formError}</Banner> : null}

      <FormField
        control={control}
        name="email"
        label={t("auth.forgot_password.email_label")}
        autoComplete="email"
        keyboardType="email-address"
      />
      {captcha.widget}
      <Button
        label={
          formState.isSubmitting
            ? t("auth.forgot_password.submit_pending")
            : t("auth.forgot_password.submit")
        }
        hint={t("auth.forgot_password.submit_hint")}
        disabled={formState.isSubmitting || !captcha.ready}
        onPress={() => {
          void handleSubmit(onSubmit)();
        }}
      />
    </AuthCard>
  );
}
