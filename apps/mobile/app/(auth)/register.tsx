import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "expo-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Text } from "react-native";

import { registerSchema, type RegisterInput } from "@forge/validation";

import { AuthCard } from "../../src/components/AuthCard";
import { Banner } from "../../src/components/Banner";
import { Button } from "../../src/components/Button";
import { FormField } from "../../src/components/FormField";
import { register } from "../../src/lib/auth";
import { getErrorMessage } from "../../src/lib/getErrorMessage";

export default function RegisterScreen() {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const { control, handleSubmit, formState } = useForm<RegisterInput>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = async (values: RegisterInput): Promise<void> => {
    setFormError(null);
    try {
      await register(values.email, values.password);
      setSubmitted(true);
    } catch (error) {
      setFormError(getErrorMessage(error, "auth.register.error_default"));
    }
  };

  if (submitted) {
    return (
      <AuthCard title={t("auth.register.success_title")}>
        <Banner>{t("auth.register.success_message")}</Banner>
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.register.back_to_login")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.register.title")}
      description={t("auth.register.description")}
      footer={
        <Text className="text-center text-sm text-slate-500">
          {t("auth.register.have_account")}{" "}
          <Link href="/login">
            <Text className="text-sm text-slate-600 underline">
              {t("auth.register.login_link")}
            </Text>
          </Link>
        </Text>
      }
    >
      {formError ? <Banner variant="error">{formError}</Banner> : null}

      <FormField
        control={control}
        name="email"
        label={t("auth.register.email_label")}
        autoComplete="email"
        keyboardType="email-address"
      />
      <FormField
        control={control}
        name="password"
        label={t("auth.register.password_label")}
        hint={t("auth.register.password_helper")}
        secureTextEntry
        autoComplete="new-password"
      />
      <Button
        label={
          formState.isSubmitting
            ? t("auth.register.submit_pending")
            : t("auth.register.submit")
        }
        hint={t("auth.register.submit_hint")}
        disabled={formState.isSubmitting}
        onPress={() => {
          void handleSubmit(onSubmit)();
        }}
      />
    </AuthCard>
  );
}
