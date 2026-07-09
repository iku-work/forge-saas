import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import type { UserPublic } from "@forge/api-client";
import { TwoFactorRequiredError, useAuth, useModules } from "@forge/hooks";
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
  Separator,
} from "@forge/ui";
import { loginSchema, type LoginInput } from "@forge/validation";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";
import { getOAuthStartUrl } from "./oauth";
import { TwoFactorChallenge } from "./TwoFactorChallenge";

export function LoginPage() {
  const { t } = useTranslation();
  const { login, completeTwoFactorLogin } = useAuth();
  const modules = useModules();
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);
  const [tempToken, setTempToken] = useState<string | null>(null);

  const form = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = async (values: LoginInput): Promise<void> => {
    setFormError(null);
    try {
      await login(values.email, values.password);
      await navigate({ to: "/" });
    } catch (error) {
      if (error instanceof TwoFactorRequiredError) {
        setTempToken(error.tempToken);
        return;
      }
      setFormError(getErrorMessage(error, "auth.login.error_default"));
    }
  };

  const handleTwoFactorSuccess = (
    accessToken: string,
    user: UserPublic,
  ): void => {
    completeTwoFactorLogin(accessToken, user);
    // _app's beforeLoad guard (apps/web/src/routes/_app.tsx) reads
    // context.auth.user, which AppRoot only passes into <RouterProvider>
    // on its next render after this setState -- navigating synchronously
    // here races that render, so the guard still sees the pre-login `null`
    // and bounces straight back to /login. Deferring to a macrotask lets
    // React commit AppRoot's re-render (and thus the router's new context)
    // first.
    setTimeout(() => {
      void navigate({ to: "/" });
    }, 0);
  };

  if (tempToken) {
    return (
      <AuthCard
        title={t("auth.two_factor_challenge.title")}
        description={t("auth.two_factor_challenge.description")}
      >
        <TwoFactorChallenge
          tempToken={tempToken}
          onSuccess={handleTwoFactorSuccess}
        />
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.login.title")}
      description={t("auth.login.description")}
      footer={
        <>
          <p>
            <Link
              to="/forgot-password"
              className="underline underline-offset-4"
            >
              {t("auth.login.forgot_password")}
            </Link>
            {" · "}
            <Link to="/magic-link" className="underline underline-offset-4">
              {t("auth.login.magic_link_cta")}
            </Link>
          </p>
          <p>
            {t("auth.login.no_account")}{" "}
            <Link to="/register" className="underline underline-offset-4">
              {t("auth.login.signup_link")}
            </Link>
          </p>
        </>
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
                <FormLabel>{t("auth.login.email_label")}</FormLabel>
                <FormControl>
                  <Input type="email" autoComplete="email" {...field} />
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
                <FormLabel>{t("auth.login.password_label")}</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="current-password" {...field} />
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
              ? t("auth.login.submit_pending")
              : t("auth.login.submit")}
          </Button>
        </form>
      </Form>

      {modules.oauth ? (
        <>
          <div className="flex items-center gap-2">
            <Separator className="flex-1" />
            <span className="text-xs text-muted-foreground">
              {t("auth.login.oauth_separator")}
            </span>
            <Separator className="flex-1" />
          </div>

          <div className="space-y-2">
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                window.location.href = getOAuthStartUrl("google");
              }}
            >
              {t("auth.login.continue_google")}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                window.location.href = getOAuthStartUrl("github");
              }}
            >
              {t("auth.login.continue_github")}
            </Button>
          </div>
        </>
      ) : null}
    </AuthCard>
  );
}
