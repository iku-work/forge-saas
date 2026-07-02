import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "expo-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Text } from "react-native";

import { requestMagicLinkApiV1AuthMagicLinkPost } from "@forge/api-client";
import { magicLinkSchema, type MagicLinkInput } from "@forge/validation";

import { AuthCard } from "../../src/components/AuthCard";
import { Banner } from "../../src/components/Banner";
import { Button } from "../../src/components/Button";
import { FormField } from "../../src/components/FormField";
import { client } from "../../src/lib/api";
import { getErrorMessage } from "../../src/lib/getErrorMessage";

// Request-only: there is no `forge://magic-link/[token]` deep link in
// T-047's scope, so unlike web's MagicLinkPage this screen never consumes a
// token itself -- the emailed link opens the web app, which is what
// actually completes a magic-link sign-in for now.
export default function MagicLinkScreen() {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const { control, handleSubmit, formState } = useForm<MagicLinkInput>({
    resolver: zodResolver(magicLinkSchema),
    defaultValues: { email: "" },
  });

  const onSubmit = async (values: MagicLinkInput): Promise<void> => {
    setFormError(null);
    try {
      await requestMagicLinkApiV1AuthMagicLinkPost({
        client,
        body: values,
        throwOnError: true,
      });
      setSubmitted(true);
    } catch (error) {
      setFormError(getErrorMessage(error, "auth.magic_link.error_default"));
    }
  };

  if (submitted) {
    return (
      <AuthCard title={t("auth.magic_link.success_title")}>
        <Banner>{t("auth.magic_link.success_message")}</Banner>
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.magic_link.back_to_login")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.magic_link.title")}
      description={t("auth.magic_link.description")}
      footer={
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("auth.magic_link.back_to_login")}
          </Text>
        </Link>
      }
    >
      {formError ? <Banner variant="error">{formError}</Banner> : null}

      <FormField
        control={control}
        name="email"
        label={t("auth.magic_link.email_label")}
        autoComplete="email"
        keyboardType="email-address"
      />
      <Button
        label={
          formState.isSubmitting
            ? t("auth.magic_link.submit_pending")
            : t("auth.magic_link.submit")
        }
        hint={t("auth.magic_link.submit_hint")}
        disabled={formState.isSubmitting}
        onPress={() => {
          void handleSubmit(onSubmit)();
        }}
      />
    </AuthCard>
  );
}
