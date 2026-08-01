import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { verifyEmailApiV1AuthVerifyEmailPost } from "@forge/api-client";
import { Alert, AlertDescription, Skeleton } from "@forge/ui";

import { useRouteLocale } from "../../lib/useRouteLocale";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

type VerifyState = "verifying" | "success" | "error";

interface VerifyEmailPageProps {
  token: string | undefined;
}

export function VerifyEmailPage({ token }: VerifyEmailPageProps) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const [state, setState] = useState<VerifyState>(
    token ? "verifying" : "error",
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Verification tokens are single-use; without this guard, React 18 Strict
  // Mode's double-invoked effect would consume the token on the first call
  // and then incorrectly report it as expired on the second.
  const requested = useRef(false);

  useEffect(() => {
    if (!token || requested.current) {
      return;
    }
    requested.current = true;
    void verifyEmailApiV1AuthVerifyEmailPost({
      body: { token },
      throwOnError: true,
    })
      .then(() => {
        setState("success");
      })
      .catch((error: unknown) => {
        setErrorMessage(
          getErrorMessage(error, "auth.verify_email.error_invalid_or_expired"),
        );
        setState("error");
      });
  }, [token]);

  if (state === "verifying") {
    return (
      <AuthCard title={t("auth.verify_email.title_verifying")}>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </AuthCard>
    );
  }

  if (state === "success") {
    return (
      <AuthCard title={t("auth.verify_email.title_success")}>
        <Alert data-testid="verify-email-success">
          <AlertDescription>
            {t("auth.verify_email.success_message")}
          </AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link
            to="/$locale/login"
            params={{ locale }}
            className="underline underline-offset-4"
            data-testid="verify-email-continue-to-login"
          >
            {t("auth.verify_email.continue_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.verify_email.title_failed")}>
      <Alert variant="destructive" data-testid="verify-email-error">
        <AlertDescription>
          {errorMessage ?? t("auth.verify_email.error_default")}
        </AlertDescription>
      </Alert>
      <p className="text-center text-sm text-muted-foreground">
        <Link
          to="/$locale/register"
          params={{ locale }}
          className="underline underline-offset-4"
        >
          {t("auth.verify_email.create_account")}
        </Link>{" "}
        {t("auth.verify_email.or")}{" "}
        <Link
          to="/$locale/login"
          params={{ locale }}
          className="underline underline-offset-4"
        >
          {t("auth.verify_email.login")}
        </Link>
      </p>
    </AuthCard>
  );
}
