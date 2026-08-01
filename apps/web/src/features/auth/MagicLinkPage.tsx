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
import { magicLinkSchema, type MagicLinkInput } from "@forge/validation";

import { useRouteLocale } from "../../lib/useRouteLocale";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

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
  // Magic link tokens are single-use; guard against Strict Mode's
  // double-invoked effect consuming the token twice.
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) {
      return;
    }
    requested.current = true;
    void consumeMagicLinkApiV1AuthMagicLinkConsumePost({
      body: { token },
      throwOnError: true,
    })
      .then(() => {
        // The consume endpoint sets the refresh cookie exactly like /login.
        // A full document navigation lets useAuth's bootstrap re-establish
        // the session from that cookie, instead of reaching into the shared
        // auth store directly from a page component.
        window.location.assign("/");
      })
      .catch((error: unknown) => {
        setErrorMessage(
          getErrorMessage(error, "auth.magic_link.error_invalid_or_expired"),
        );
      });
  }, [token]);

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
