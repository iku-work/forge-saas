import { useEffect, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import {
  acceptInvitationApiV1InvitationsTokenAcceptPost,
  previewInvitationApiV1InvitationsTokenGet,
  type InvitationPreview,
} from "@forge/api-client";
import { useAuth } from "@forge/hooks";
import { Alert, AlertDescription, Button, Skeleton } from "@forge/ui";

import { useRouteLocale } from "../../lib/useRouteLocale";

import { AuthCard } from "./AuthCard";
import { getErrorMessage } from "./getErrorMessage";

interface AcceptInvitationPageProps {
  token: string;
}

export function AcceptInvitationPage({ token }: AcceptInvitationPageProps) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const { user, isLoading: authLoading } = useAuth();
  const navigate = useNavigate();

  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isAccepting, setIsAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void previewInvitationApiV1InvitationsTokenGet({
      path: { token },
      throwOnError: true,
    })
      .then(({ data }) => {
        if (!cancelled) {
          setPreview(data.data ?? null);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setPreviewError(
            getErrorMessage(
              error,
              "tenancy.invitation_accept.unavailable_message",
            ),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const handleAccept = async (): Promise<void> => {
    setAcceptError(null);
    setIsAccepting(true);
    try {
      await acceptInvitationApiV1InvitationsTokenAcceptPost({
        path: { token },
        throwOnError: true,
      });
      await navigate({ to: "/$locale", params: { locale } });
    } catch (error) {
      setAcceptError(
        getErrorMessage(error, "tenancy.invitation_accept.error_default"),
      );
      setIsAccepting(false);
    }
  };

  if (previewError) {
    return (
      <AuthCard title={t("tenancy.invitation_accept.unavailable_title")}>
        <Alert variant="destructive">
          <AlertDescription>{previewError}</AlertDescription>
        </Alert>
        <p className="text-center text-sm text-muted-foreground">
          <Link
            to="/$locale/login"
            params={{ locale }}
            className="underline underline-offset-4"
          >
            {t("tenancy.invitation_accept.back_to_login")}
          </Link>
        </p>
      </AuthCard>
    );
  }

  if (!preview || authLoading) {
    return (
      <AuthCard title={t("tenancy.invitation_accept.loading_title")}>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={t("tenancy.invitation_accept.title")}
      description={t("tenancy.invitation_accept.join_description", {
        org_name: preview.org_name,
        role: preview.role,
      })}
    >
      {acceptError ? (
        <Alert variant="destructive">
          <AlertDescription>{acceptError}</AlertDescription>
        </Alert>
      ) : null}

      {user ? (
        <Button
          className="w-full"
          onClick={() => {
            void handleAccept();
          }}
          disabled={isAccepting}
          data-testid="accept-invitation-button"
        >
          {isAccepting
            ? t("tenancy.invitation_accept.accept_button_pending")
            : t("tenancy.invitation_accept.accept_button")}
        </Button>
      ) : (
        <div className="space-y-2">
          <p className="text-center text-sm text-muted-foreground">
            {t("tenancy.invitation_accept.not_logged_in", {
              email: preview.email,
            })}
          </p>
          <Button asChild className="w-full">
            <Link
              to="/$locale/login"
              params={{ locale }}
              data-testid="accept-invitation-login-link"
            >
              {t("tenancy.invitation_accept.login")}
            </Link>
          </Button>
          <Button asChild variant="outline" className="w-full">
            <Link
              to="/$locale/register"
              params={{ locale }}
              data-testid="accept-invitation-signup-link"
            >
              {t("tenancy.invitation_accept.signup")}
            </Link>
          </Button>
        </div>
      )}
    </AuthCard>
  );
}
