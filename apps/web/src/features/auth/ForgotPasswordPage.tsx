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

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const form = useForm<ForgotPasswordInput>({
    resolver: zodResolver(forgotPasswordSchema),
    defaultValues: { email: "" },
  });

  const onSubmit = async (values: ForgotPasswordInput): Promise<void> => {
    setFormError(null);
    try {
      await forgotPasswordApiV1AuthForgotPasswordPost({
        body: values,
        throwOnError: true,
      });
      setSubmitted(true);
    } catch (error) {
      setFormError(
        getErrorMessage(error, "auth.forgot_password.error_default"),
      );
    }
  };

  if (submitted) {
    return (
      <AuthCard title={t("auth.forgot_password.success_title")}>
        <Alert>
          <AlertDescription>
            {t("auth.forgot_password.success_message")}
          </AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link to="/login" className="underline underline-offset-4">
            {t("auth.forgot_password.back_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.forgot_password.title")}
      description={t("auth.forgot_password.description")}
      footer={
        <p>
          <Link to="/login" className="underline underline-offset-4">
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
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            {form.formState.isSubmitting
              ? t("auth.forgot_password.submit_pending")
              : t("auth.forgot_password.submit")}
          </Button>
        </form>
      </Form>
    </AuthCard>
  );
}
