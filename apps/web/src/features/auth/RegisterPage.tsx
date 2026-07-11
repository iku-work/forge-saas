import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { useAuth } from "@forge/hooks";
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
  PasswordInput,
  PasswordStrengthMeter,
} from "@forge/ui";
import { registerSchema, type RegisterInput } from "@forge/validation";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

export function RegisterPage() {
  const { t } = useTranslation();
  const { register } = useAuth();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const form = useForm<RegisterInput>({
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
        <Alert data-testid="register-success">
          <AlertDescription>
            {t("auth.register.success_message")}
          </AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link to="/login" className="underline underline-offset-4">
            {t("auth.register.back_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.register.title")}
      description={t("auth.register.description")}
      footer={
        <p>
          {t("auth.register.have_account")}{" "}
          <Link to="/login" className="underline underline-offset-4">
            {t("auth.register.login_link")}
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
                <FormLabel>{t("auth.register.email_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    autoComplete="email"
                    data-testid="register-email-input"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("auth.register.password_label")}</FormLabel>
                <FormControl>
                  <PasswordInput
                    autoComplete="new-password"
                    data-testid="register-password-input"
                    {...field}
                  />
                </FormControl>
                <p className="text-xs text-muted-foreground">
                  {t("auth.register.password_helper")}
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
            data-testid="register-submit"
          >
            {form.formState.isSubmitting
              ? t("auth.register.submit_pending")
              : t("auth.register.submit")}
          </Button>
        </form>
      </Form>
    </AuthCard>
  );
}
