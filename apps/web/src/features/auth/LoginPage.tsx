import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { useAuth, useModules } from "@forge/hooks";
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

export function LoginPage() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const modules = useModules();
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);

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
      setFormError(getErrorMessage(error, "auth.login.error_default"));
    }
  };

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
