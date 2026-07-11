import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { cancelEmailChangeApiV1AuthChangeEmailCancelPost } from "@forge/api-client";
import { Alert, AlertDescription, Skeleton } from "@forge/ui";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

type CancelState = "verifying" | "success" | "error";

interface CancelEmailChangePageProps {
  token: string | undefined;
}

// T-226: mirrors ConfirmEmailChangePage's shape exactly -- same single-use
// token consumption flow, just against POST /auth/change-email/cancel. This
// is the link mailed to the *old* address alongside the
// email_change_requested notification, so a real account owner can stop a
// request they didn't make before the (separately mailed) confirm link is
// ever clicked.
export function CancelEmailChangePage({ token }: CancelEmailChangePageProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<CancelState>(token ? "verifying" : "error");
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
    void cancelEmailChangeApiV1AuthChangeEmailCancelPost({
      body: { token },
      throwOnError: true,
    })
      .then(() => {
        setState("success");
      })
      .catch((error: unknown) => {
        setErrorMessage(
          getErrorMessage(error, "auth.cancel_email_change.error_invalid_or_expired"),
        );
        setState("error");
      });
  }, [token]);

  if (state === "verifying") {
    return (
      <AuthCard title={t("auth.cancel_email_change.title_verifying")}>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </AuthCard>
    );
  }

  if (state === "success") {
    return (
      <AuthCard title={t("auth.cancel_email_change.title_success")}>
        <Alert>
          <AlertDescription>{t("auth.cancel_email_change.success_message")}</AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link to="/login" className="underline underline-offset-4">
            {t("auth.cancel_email_change.continue_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.cancel_email_change.title_failed")}>
      <Alert variant="destructive">
        <AlertDescription>
          {errorMessage ?? t("auth.cancel_email_change.error_default")}
        </AlertDescription>
      </Alert>
    </AuthCard>
  );
}
