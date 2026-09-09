import { useRef, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import {
  ssoHintApiV1AuthSsoHintPost,
  type UserPublic,
} from "@forge/api-client";
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

import { useRouteLocale } from "../../lib/useRouteLocale";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";
import { getOAuthStartUrl } from "./oauth";
import { getSsoStartUrl } from "./sso";
import { TwoFactorChallenge } from "./TwoFactorChallenge";

interface SsoHint {
  orgSlug: string;
  providerLabel: string;
}

export function LoginPage() {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const { login, completeTwoFactorLogin } = useAuth();
  const modules = useModules();
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);
  const [tempToken, setTempToken] = useState<string | null>(null);
  const [ssoHint, setSsoHint] = useState<SsoHint | null>(null);
  const [isCheckingSso, setIsCheckingSso] = useState(false);
  // Set by "use a password instead", and never cleared by a hint arriving --
  // somebody who has asked for the password field keeps it for this visit.
  const [passwordRequested, setPasswordRequested] = useState(false);
  // The address the in-flight hint request was made for. A ref, not state:
  // it exists only to discard a response that has been overtaken by more
  // typing, and re-rendering on every keystroke to track that would be the
  // tail wagging the dog.
  const pendingHintFor = useRef<string | null>(null);

  const form = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  // Asked on blur rather than on every keystroke: the endpoint is behind the
  // auth rate limit, and a half-typed address is not a domain.
  const checkForSso = (email: string): void => {
    const address = email.trim().toLowerCase();
    if (!modules.enterpriseSso || !address.includes("@")) {
      return;
    }
    if (address === pendingHintFor.current) {
      return;
    }
    pendingHintFor.current = address;
    setIsCheckingSso(true);
    void ssoHintApiV1AuthSsoHintPost({
      body: { email: address },
      throwOnError: true,
    })
      .then(({ data }) => {
        // They may have kept typing while this was in flight; a hint for an
        // address they have already replaced would offer the wrong provider.
        if (pendingHintFor.current !== address) {
          return;
        }
        const hint = data.data;
        setSsoHint(
          hint?.sso_available && hint.org_slug && hint.provider_label
            ? { orgSlug: hint.org_slug, providerLabel: hint.provider_label }
            : null,
        );
      })
      .catch(() => {
        // Deliberately silent. The hint is a convenience, and the endpoint it
        // calls is rate limited; a failed lookup must leave the password
        // field exactly as it was rather than put an error in front of
        // somebody who was only trying to log in.
        if (pendingHintFor.current === address) {
          setSsoHint(null);
        }
      })
      .finally(() => {
        if (pendingHintFor.current === address) {
          setIsCheckingSso(false);
        }
      });
  };

  const onSubmit = async (values: LoginInput): Promise<void> => {
    setFormError(null);
    try {
      await login(values.email, values.password);
      await navigate({ to: "/$locale", params: { locale } });
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
      void navigate({ to: "/$locale", params: { locale } });
    }, 0);
  };

  const showSso = ssoHint !== null && !passwordRequested;

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
              to="/$locale/forgot-password"
              params={{ locale }}
              className="underline underline-offset-4"
            >
              {t("auth.login.forgot_password")}
            </Link>
            {" · "}
            <Link
              to="/$locale/magic-link"
              params={{ locale }}
              className="underline underline-offset-4"
            >
              {t("auth.login.magic_link_cta")}
            </Link>
          </p>
          <p>
            {t("auth.login.no_account")}{" "}
            <Link
              to="/$locale/register"
              params={{ locale }}
              className="underline underline-offset-4"
            >
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
                  <Input
                    type="email"
                    autoComplete="email"
                    data-testid="login-email-input"
                    {...field}
                    onBlur={(event) => {
                      field.onBlur();
                      checkForSso(event.target.value);
                    }}
                    onChange={(event) => {
                      field.onChange(event);
                      // A hint belongs to the domain it was asked about. Once
                      // that changes, the button on screen would send them to
                      // somebody else's identity provider.
                      setSsoHint(null);
                    }}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {showSso ? (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                {t("auth.login.sso_detected", {
                  provider: ssoHint.providerLabel,
                })}
              </p>
              <Button
                type="button"
                className="w-full"
                data-testid="login-sso-continue"
                onClick={() => {
                  window.location.href = getSsoStartUrl(ssoHint.orgSlug);
                }}
              >
                {t("auth.login.sso_continue", {
                  provider: ssoHint.providerLabel,
                })}
              </Button>
              {/* The hint is a suggestion, not enforcement -- enforcement
                  lives on the server and answers with `errors.sso_required`.
                  Somebody who genuinely has a password here (the last
                  remaining owner, on a day their IdP is broken) has to be
                  able to reach the field, and this is the only way back. */}
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                data-testid="login-sso-use-password"
                onClick={() => {
                  setPasswordRequested(true);
                }}
              >
                {t("auth.login.sso_use_password")}
              </Button>
            </div>
          ) : (
            <>
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("auth.login.password_label")}</FormLabel>
                    <FormControl>
                      <PasswordInput
                        autoComplete="current-password"
                        data-testid="login-password-input"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button
                type="submit"
                className="w-full"
                disabled={form.formState.isSubmitting}
                data-testid="login-submit"
              >
                {form.formState.isSubmitting
                  ? t("auth.login.submit_pending")
                  : t("auth.login.submit")}
              </Button>
              {/* Announced rather than blocking: the lookup runs beside the
                  password field, and submitting during it still works. */}
              <p
                role="status"
                aria-live="polite"
                className="text-xs text-muted-foreground"
              >
                {isCheckingSso ? t("auth.login.sso_checking") : ""}
              </p>
            </>
          )}
        </form>
      </Form>

      {modules.oauth && !showSso ? (
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
