import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { forgotPasswordApiV1AuthForgotPasswordPost } from "@forge/api-client";
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
  Input,
} from "@forge/ui";
import {
  forgotPasswordSchema,
  type ForgotPasswordInput,
} from "@forge/validation";

import { useRouteLocale } from "../../lib/useRouteLocale";

import { AuthLayout } from "./AuthLayout";
import { getErrorMessage } from "./getErrorMessage";
import { useCaptcha } from "./useCaptcha";

export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const captcha = useCaptcha();

  const form = useForm<ForgotPasswordInput>({
    resolver: zodResolver(forgotPasswordSchema),
    defaultValues: { email: "" },
  });

  const onSubmit = async (values: ForgotPasswordInput): Promise<void> => {
    setFormError(null);
    try {
      await forgotPasswordApiV1AuthForgotPasswordPost({
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
      <AuthLayout title={t("auth.forgot_password.success_title")}>
        <Alert>
          <AlertDescription>
            {t("auth.forgot_password.success_message")}
          </AlertDescription>
        </Alert>
        <p className="text-sm text-foreground-secondary">
          <Link
            to="/$locale/login"
            params={{ locale }}
            className="underline underline-offset-4"
          >
            {t("auth.forgot_password.back_to_login")}
          </Link>
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={t("auth.forgot_password.title")}
      description={t("auth.forgot_password.description")}
      footer={
        <p>
          <Link
            to="/$locale/login"
            params={{ locale }}
            className="underline underline-offset-4"
          >
            {t("auth.forgot_password.back_to_login")}
          </Link>
        </p>
      }
    >
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
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("auth.forgot_password.email_label")}</FormLabel>
                <FormControl>
                  <Input type="email" autoComplete="email" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {captcha.widget}
          <Button
            type="submit"
            size="lg"
            className="w-full"
            disabled={form.formState.isSubmitting || !captcha.ready}
          >
            {form.formState.isSubmitting
              ? t("auth.forgot_password.submit_pending")
              : t("auth.forgot_password.submit")}
          </Button>
        </form>
      </Form>
    </AuthLayout>
  );
}
