import { useEffect, useState } from "react";
import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import { ActivityIndicator, Text, View } from "react-native";

import {
  acceptInvitationApiV1InvitationsTokenAcceptPost,
  previewInvitationApiV1InvitationsTokenGet,
  type InvitationPreview,
} from "@forge/api-client";

import { AuthCard } from "../../src/components/AuthCard";
import { Banner } from "../../src/components/Banner";
import { Button } from "../../src/components/Button";
import { client } from "../../src/lib/api";
import { useSession } from "../../src/lib/auth";
import { getErrorMessage } from "../../src/lib/getErrorMessage";

// Outside both the (auth) and (app) groups -- this screen must be reachable
// whether or not the visitor is currently logged in, which a guarded group
// layout (see app/(app)/_layout.tsx) would prevent.
export default function InvitationScreen() {
  const { t } = useTranslation();
  const { token } = useLocalSearchParams<{ token: string }>();
  const { user, isLoading: sessionLoading } = useSession();
  const router = useRouter();

  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isAccepting, setIsAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void previewInvitationApiV1InvitationsTokenGet({
      client,
      path: { token },
      throwOnError: true,
    })
      .then(({ data }) => {
        if (!cancelled) setPreview(data.data ?? null);
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
        client,
        path: { token },
        throwOnError: true,
      });
      router.replace("/dashboard");
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
        <Banner variant="error">{previewError}</Banner>
        <Link href="/login">
          <Text className="text-center text-sm text-slate-600 underline">
            {t("tenancy.invitation_accept.back_to_login")}
          </Text>
        </Link>
      </AuthCard>
    );
  }

  if (!preview || sessionLoading) {
    return (
      <AuthCard title={t("tenancy.invitation_accept.loading_title")}>
        <ActivityIndicator />
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
      {acceptError ? <Banner variant="error">{acceptError}</Banner> : null}

      {user ? (
        <Button
          label={
            isAccepting
              ? t("tenancy.invitation_accept.accept_button_pending")
              : t("tenancy.invitation_accept.accept_button")
          }
          hint={t("tenancy.invitation_accept.accept_hint")}
          disabled={isAccepting}
          onPress={() => {
            void handleAccept();
          }}
        />
      ) : (
        <View className="gap-2">
          <Text className="text-center text-sm text-slate-500">
            {t("tenancy.invitation_accept.not_logged_in", {
              email: preview.email,
            })}
          </Text>
          <Link href="/login">
            <Text className="text-center text-sm text-slate-600 underline">
              {t("tenancy.invitation_accept.login")}
            </Text>
          </Link>
          <Link href="/register">
            <Text className="text-center text-sm text-slate-600 underline">
              {t("tenancy.invitation_accept.signup")}
            </Text>
          </Link>
        </View>
      )}
    </AuthCard>
  );
}
