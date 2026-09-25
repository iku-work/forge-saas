import { useEffect, useRef, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import {
  consumeMagicLinkApiV1AuthMagicLinkConsumePost,
  requestMagicLinkApiV1AuthMagicLinkPost,
} from "@forge/api-client";
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
  Skeleton,
} from "@forge/ui";
import { useAuth } from "@forge/hooks";
import { magicLinkSchema, type MagicLinkInput } from "@forge/validation";

import { useRouteLocale } from "../../lib/useRouteLocale";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";
import { TwoFactorChallenge } from "./TwoFactorChallenge";

interface MagicLinkPageProps {
  token: string | undefined;
}

export function MagicLinkPage({ token }: MagicLinkPageProps) {
  if (token) {
    return <MagicLinkConsume token={token} />;
  }
  return <MagicLinkRequestForm />;
}

function MagicLinkConsume({ token }: { token: string }) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [tempToken, setTempToken] = useState<string | null>(null);
  const { completeTwoFactorLogin } = useAuth();
  // Magic link tokens are single-use; guard against Strict Mode's
  // double-invoked effect consuming the token twice.
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) {
      return;
    }
    requested.current = true;

    const consume = async (): Promise<void> => {
      const { data: envelope } =
        await consumeMagicLinkApiV1AuthMagicLinkConsumePost({
          body: { token },
          throwOnError: true,
        });
      const payload = envelope.data;
      // A magic link proves control of an inbox. For an account with a
      // second factor enrolled that is one factor, not two, so the endpoint
      // answers with a challenge instead of a session -- the same shape
      // /login uses, finished by the same component.
      //
      // Narrowed on `temp_token` for the reason useAuth's login() gives:
      // `pending_2fa` is optional in the generated type, so it can be absent
      // at runtime even on the branch that declares it. The `typeof` is what
      // makes it a `string` -- `in` alone narrows the object but leaves the
      // property `unknown` against this generated union.
      if (
        payload &&
        "temp_token" in payload &&
        typeof payload.temp_token === "string"
      ) {
        setTempToken(payload.temp_token);
        return;
      }
      // Otherwise the consume endpoint set the refresh cookie exactly like
      // /login. A full document navigation lets useAuth's bootstrap
      // re-establish the session from that cookie, instead of reaching into
      // the shared auth store directly from a page component.
      window.location.assign("/");
    };

    void consume().catch((error: unknown) => {
      setErrorMessage(
        getErrorMessage(error, "auth.magic_link.error_invalid_or_expired"),
      );
    });
  }, [token]);

  if (tempToken) {
    return (
      <AuthCard
        title={t("auth.two_factor_challenge.title")}
        description={t("auth.two_factor_challenge.description")}
      >
        <TwoFactorChallenge
          tempToken={tempToken}
          onSuccess={(accessToken, user) => {
            completeTwoFactorLogin(accessToken, user);
            // A document navigation rather than a router navigate: this
            // component is reached from an emailed link with no router
            // context to speak of, and the reload is what lets useAuth
            // bootstrap from the refresh cookie the 2FA step just set.
            window.location.assign("/");
          }}
        />
      </AuthCard>
    );
  }

  if (errorMessage) {
    return (
      <AuthCard title={t("auth.magic_link.title_failed")}>
        <Alert variant="destructive">
          <AlertDescription>{errorMessage}</AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link
            to="/$locale/magic-link"
            params={{ locale }}
            className="underline underline-offset-4"
          >
            {t("auth.magic_link.request_new")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.magic_link.title_consuming")}>
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
    </AuthCard>
  );
}

function MagicLinkRequestForm() {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const form = useForm<MagicLinkInput>({
    resolver: zodResolver(magicLinkSchema),
    defaultValues: { email: "" },
  });

  const onSubmit = async (values: MagicLinkInput): Promise<void> => {
    setFormError(null);
    try {
      await requestMagicLinkApiV1AuthMagicLinkPost({
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
        <Alert>
          <AlertDescription>
            {t("auth.magic_link.success_message")}
          </AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link
            to="/$locale/login"
            params={{ locale }}
            className="underline underline-offset-4"
          >
            {t("auth.magic_link.back_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("auth.magic_link.title")}
      description={t("auth.magic_link.description")}
      footer={
        <p>
          <Link
            to="/$locale/login"
            params={{ locale }}
            className="underline underline-offset-4"
          >
            {t("auth.magic_link.back_to_login")}
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
                <FormLabel>{t("auth.magic_link.email_label")}</FormLabel>
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
              ? t("auth.magic_link.submit_pending")
              : t("auth.magic_link.submit")}
          </Button>
        </form>
      </Form>
    </AuthCard>
  );
}
