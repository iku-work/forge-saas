import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
// `Trans`, not string concatenation: the sentence has two inline links,
// and splitting it into fragments would fix English word order for every
// language. The only use of Trans in this app so far, for that reason.
import { Trans, useTranslation } from "react-i18next";

import { useAuth, useMarketingUrl, useSignupDisabled } from "@forge/hooks";
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

import { useRouteLocale } from "../../lib/useRouteLocale";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

export function RegisterPage() {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const marketingUrl = useMarketingUrl();
  const { register } = useAuth();
  const signupDisabled = useSignupDisabled();
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

  // T-263: the server refuses POST /auth/register with a 403 while
  // `signup_disabled` is set, so this only spares the visitor filling in a
  // form that was never going to work. Deliberately not a disabled button --
  // a form you can type into but not submit is worse than no form.
  if (signupDisabled) {
    return (
      <AuthCard title={t("auth.register.title")}>
        <Alert data-testid="register-closed">
          <AlertDescription>
            {t("auth.register.signup_closed")}
          </AlertDescription>
        </Alert>
      </AuthCard>
    );
  }

  if (submitted) {
    return (
      <AuthCard title={t("auth.register.success_title")}>
        <Alert data-testid="register-success">
          <AlertDescription>
            {t("auth.register.success_message")}
          </AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link
            to="/$locale/login"
            params={{ locale }}
            className="underline underline-offset-4"
          >
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
        <div className="space-y-2">
          <p>
            {t("auth.register.have_account")}{" "}
            <Link
              to="/$locale/login"
              params={{ locale }}
              className="underline underline-offset-4"
            >
              {t("auth.register.login_link")}
            </Link>
          </p>
          {/* UX-02: the terms assert that creating an account is acceptance
              of them, and this screen linked to neither document. Asking
              someone to agree to something you have not shown them is not a
              detail -- it is the part that makes the assertion worthless.

              Rendered only when there is somewhere to link to: the pages
              live on the marketing host, so they do not exist at all in a
              product that switched that module off. */}
          {marketingUrl ? (
            <p className="text-xs text-muted-foreground">
              <Trans
                i18nKey="auth.register.legal_notice"
                components={{
                  terms: (
                    <a
                      href={`${marketingUrl}/${locale}/terms`}
                      target="_blank"
                      rel="noreferrer"
                      className="underline underline-offset-4"
                    />
                  ),
                  privacy: (
                    <a
                      href={`${marketingUrl}/${locale}/privacy`}
                      target="_blank"
                      rel="noreferrer"
                      className="underline underline-offset-4"
                    />
                  ),
                }}
              />
            </p>
          ) : null}
        </div>
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
