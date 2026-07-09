import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { confirmEmailChangeApiV1AuthChangeEmailConfirmPost } from "@forge/api-client";
import { Alert, AlertDescription, Skeleton } from "@forge/ui";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

type ConfirmState = "verifying" | "success" | "error";

interface ConfirmEmailChangePageProps {
  token: string | undefined;
}

// T-218 (minimal implementation -- see T-226 for the production-ready
// follow-up): mirrors VerifyEmailPage's shape exactly -- same single-use
// token confirmation flow, just against POST /auth/change-email/confirm.
export function ConfirmEmailChangePage({ token }: ConfirmEmailChangePageProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<ConfirmState>(token ? "verifying" : "error");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Single-use token -- without this guard, React 18 Strict Mode's
  // double-invoked effect would consume the token on the first call and
  // then incorrectly report it as expired on the second.
  const requested = useRef(false);

  useEffect(() => {
    if (!token || requested.current) {
      return;
    }
    requested.current = true;
    void confirmEmailChangeApiV1AuthChangeEmailConfirmPost({
      body: { token },
      throwOnError: true,
    })
      .then(() => {
        setState("success");
      })
      .catch((error: unknown) => {
        setErrorMessage(
          getErrorMessage(error, "auth.confirm_email_change.error_invalid_or_expired"),
        );
        setState("error");
      });
  }, [token]);

  if (state === "verifying") {
    return (
      <AuthCard title={t("auth.confirm_email_change.title_verifying")}>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </AuthCard>
    );
  }

  if (state === "success") {
    return (
      <AuthCard title={t("auth.confirm_email_change.title_success")}>
        <Alert>
          <AlertDescription>{t("auth.confirm_email_change.success_message")}</AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link to="/login" className="underline underline-offset-4">
            {t("auth.confirm_email_change.continue_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.confirm_email_change.title_failed")}>
      <Alert variant="destructive">
        <AlertDescription>
          {errorMessage ?? t("auth.confirm_email_change.error_default")}
        </AlertDescription>
      </Alert>
    </AuthCard>
  );
}
