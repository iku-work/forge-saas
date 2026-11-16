import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import {
  acceptMyInvitationApiV1MeInvitationsInvitationIdAcceptPost,
  declineMyInvitationApiV1MeInvitationsInvitationIdDeclinePost,
  listMyInvitationsApiV1MeInvitationsGet,
  listMyInvitationsApiV1MeInvitationsGetQueryKey,
} from "@forge/api-client";
import type { MyInvitation } from "@forge/api-client";
import { useAuth, useOrg } from "@forge/hooks";
import { Alert, AlertDescription, Button, Skeleton, toast } from "@forge/ui";
import { formatDate } from "@forge/utils";

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";

interface MyInvitationsListProps {
  /** Render nothing at all while loading or when there is nothing to show:
   *  for the first-organization screen, where this is an extra. */
  quietWhenEmpty?: boolean;
}

/**
 * Open invitations to the signed-in user's own address, each with Accept and
 * Decline. Accepting joins the org and opens it; declining withdraws the
 * invitation without telling the inviter.
 */
export function MyInvitationsList({
  quietWhenEmpty = false,
}: MyInvitationsListProps) {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const { refreshOrgs, switchOrg } = useOrg();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const locale = useRouteLocale();
  const queryKey = listMyInvitationsApiV1MeInvitationsGetQueryKey();

  const invitationsQuery = useQuery({
    queryKey,
    queryFn: async () => {
      const { data } = await listMyInvitationsApiV1MeInvitationsGet({
        throwOnError: true,
      });
      return data.data ?? [];
    },
  });

  const accept = useMutation({
    mutationFn: async (invitation: MyInvitation) => {
      await acceptMyInvitationApiV1MeInvitationsInvitationIdAcceptPost({
        path: { invitation_id: invitation.id },
        throwOnError: true,
      });
    },
    onSuccess: async (_data, invitation) => {
      toast(
        t("tenancy.my_invitations.accepted", { org_name: invitation.org_name }),
      );
      await queryClient.invalidateQueries({ queryKey });
      await refreshOrgs();
      switchOrg(invitation.org_id);
      await navigate({ to: "/$locale", params: { locale } });
    },
    onError: (error) => {
      toast.error(
        getErrorMessage(error, "tenancy.my_invitations.error_action"),
      );
    },
  });

  const decline = useMutation({
    mutationFn: async (invitation: MyInvitation) => {
      await declineMyInvitationApiV1MeInvitationsInvitationIdDeclinePost({
        path: { invitation_id: invitation.id },
        throwOnError: true,
      });
    },
    onSuccess: async () => {
      toast(t("tenancy.my_invitations.declined"));
      await queryClient.invalidateQueries({ queryKey });
    },
    onError: (error) => {
      toast.error(
        getErrorMessage(error, "tenancy.my_invitations.error_action"),
      );
    },
  });

  const invitations = invitationsQuery.data ?? [];
  const busyId = accept.isPending
    ? accept.variables.id
    : decline.isPending
      ? decline.variables.id
      : null;

  if (
    quietWhenEmpty &&
    (invitationsQuery.isLoading || invitations.length === 0)
  ) {
    return null;
  }

  if (invitationsQuery.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (invitationsQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {getErrorMessage(
            invitationsQuery.error,
            "tenancy.my_invitations.error_load",
          )}
        </AlertDescription>
      </Alert>
    );
  }

  if (invitations.length === 0) {
    return (
      <p
        className="text-sm text-muted-foreground"
        data-testid="no-my-invitations"
      >
        {user?.email_verified_at
          ? t("tenancy.my_invitations.none")
          : t("tenancy.my_invitations.verify_note")}
      </p>
    );
  }

  return (
    <ul
      aria-label={t("tenancy.my_invitations.list_label")}
      className="divide-y rounded-lg border"
    >
      {invitations.map((invitation) => {
        const busy = busyId === invitation.id;
        return (
          <li
            key={invitation.id}
            data-testid="my-invitation-row"
            className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
          >
            <div className="min-w-0">
              <p className="truncate font-medium">{invitation.org_name}</p>
              <p className="flex flex-wrap gap-x-3 text-sm text-muted-foreground">
                <span>
                  {t("tenancy.my_invitations.join_as", {
                    role: t(`tenancy.members.role.${invitation.role}`),
                  })}
                </span>
                <span>
                  {t("tenancy.my_invitations.expires", {
                    date: formatDate(invitation.expires_at, i18n.language),
                  })}
                </span>
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busyId !== null}
                onClick={() => {
                  decline.mutate(invitation);
                }}
              >
                {busy && decline.isPending
                  ? t("tenancy.my_invitations.decline_pending")
                  : t("tenancy.my_invitations.decline")}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={busyId !== null}
                onClick={() => {
                  accept.mutate(invitation);
                }}
              >
                {busy && accept.isPending
                  ? t("tenancy.my_invitations.accept_pending")
                  : t("tenancy.my_invitations.accept")}
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
