import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { resetPasswordApiV1AuthResetPasswordPost } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  PasswordInput,
  PasswordStrengthMeter,
} from "@forge/ui";
import {
  passwordResetSchema,
  type PasswordResetInput,
} from "@forge/validation";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

interface ResetPasswordPageProps {
  token: string | undefined;
}

export function ResetPasswordPage({ token }: ResetPasswordPageProps) {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const form = useForm<PasswordResetInput>({
    resolver: zodResolver(passwordResetSchema),
    defaultValues: { token: token ?? "", newPassword: "" },
  });

  if (!token) {
    return (
      <AuthCard title={t("auth.reset_password.invalid_title")}>
        <Alert variant="destructive">
          <AlertDescription>
            {t("auth.reset_password.invalid_message")}
          </AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link to="/forgot-password" className="underline underline-offset-4">
            {t("auth.reset_password.request_new")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  const onSubmit = async (values: PasswordResetInput): Promise<void> => {
    setFormError(null);
    try {
      await resetPasswordApiV1AuthResetPasswordPost({
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
        <Alert>
          <AlertDescription>
            {t("auth.reset_password.success_message")}
          </AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link to="/login" className="underline underline-offset-4">
            {t("auth.reset_password.continue_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.reset_password.title")}>
      {formError ? (
        <Alert variant="destructive">
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      <Form {...form}>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            void form.handleSubmit(onSubmit)(event);
          }}
          noValidate
        >
          <input type="hidden" {...form.register("token")} />
          <FormField
            control={form.control}
            name="newPassword"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {t("auth.reset_password.new_password_label")}
                </FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="new-password" {...field} />
                </FormControl>
                <p className="text-xs text-muted-foreground">
                  {t("auth.reset_password.password_helper")}
                </p>
                <PasswordStrengthMeter password={field.value} />
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            {form.formState.isSubmitting
              ? t("auth.reset_password.submit_pending")
              : t("auth.reset_password.submit")}
          </Button>
        </form>
      </Form>
    </AuthCard>
  );
}
