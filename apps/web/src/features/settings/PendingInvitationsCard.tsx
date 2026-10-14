import { useCallback, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  listInvitationsApiV1OrgsOrgIdInvitationsGet,
  listInvitationsApiV1OrgsOrgIdInvitationsGetInfiniteQueryKey,
  resendInvitationApiV1OrgsOrgIdInvitationsInvitationIdResendPost,
  revokeInvitationApiV1OrgsOrgIdInvitationsInvitationIdDelete,
} from "@forge/api-client";
import type { InvitationPublic } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  ConfirmDialog,
  DataListColumns,
  DataListRow,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  RowActionsButton,
  SettingsSection,
  Skeleton,
  StatusPill,
  toast,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { VirtualList } from "../../components/VirtualList";
import { INVITATIONS_LIST_SCROLL_ID } from "../../lib/scrollRestorationIds";
import { getErrorMessage } from "../auth/getErrorMessage";

// Same fixed-height row + capped scroll port as the member list above it
// (see MembersSettingsPage), so the two lists on this page scroll and
// paginate identically.
const ESTIMATED_ROW_HEIGHT_PX = 52;
const INVITATION_COLUMNS =
  "grid-cols-[minmax(0,1fr)_6.5rem_minmax(0,9rem)_2rem]";
const MAX_LIST_HEIGHT_PX = 420;

function isExpired(invitation: InvitationPublic): boolean {
  return new Date(invitation.expires_at).getTime() <= Date.now();
}

export interface PendingInvitationsCardProps {
  orgId: string;
  /** Restored scroll offset for this list (T-241) -- supplied by the route,
   *  the only place that can call useElementScrollRestoration. */
  initialScrollOffset?: number;
}

/**
 * Invitations that have been sent but not yet accepted.
 *
 * Rendered only for MEMBERS_MANAGE holders: the backend gates
 * GET /orgs/{id}/invitations on that permission (unlike the member list's
 * MEMBERS_READ) because the response is keyed on invitee email addresses, so
 * for anyone else this card would be an error box rather than a feature.
 * MembersSettingsPage is what enforces that -- see its `canManage` check.
 */
export function PendingInvitationsCard({
  orgId,
  initialScrollOffset,
}: PendingInvitationsCardProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [pendingRevoke, setPendingRevoke] = useState<InvitationPublic | null>(
    null,
  );
  const listKey = listInvitationsApiV1OrgsOrgIdInvitationsGetInfiniteQueryKey({
    path: { org_id: orgId },
  });

  const resend = useMutation({
    mutationFn: async (invitation: InvitationPublic) => {
      await resendInvitationApiV1OrgsOrgIdInvitationsInvitationIdResendPost({
        path: { org_id: orgId, invitation_id: invitation.id },
        throwOnError: true,
      });
    },
    onSuccess: async (_data, invitation) => {
      // The row's expiry moved: a resent invitation is good for another week.
      await queryClient.invalidateQueries({ queryKey: listKey });
      toast.success(
        t("tenancy.invitations.resent", { email: invitation.email }),
      );
    },
    onError: (error) => {
      toast.error(getErrorMessage(error, "tenancy.invitations.error_resend"));
    },
  });

  const revoke = useMutation({
    mutationFn: async (invitation: InvitationPublic) => {
      await revokeInvitationApiV1OrgsOrgIdInvitationsInvitationIdDelete({
        path: { org_id: orgId, invitation_id: invitation.id },
        throwOnError: true,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: listKey });
    },
    onError: (error) => {
      toast.error(getErrorMessage(error, "tenancy.invitations.error_revoke"));
    },
  });

  // Raw SDK call rather than the generated *GetInfiniteOptions() helper --
  // see CreateOrgForm.tsx for why. The query key function alone is fine to
  // reuse since it doesn't make a network call.
  const invitationsQuery = useInfiniteQuery({
    queryKey: listInvitationsApiV1OrgsOrgIdInvitationsGetInfiniteQueryKey({
      path: { org_id: orgId },
    }),
    queryFn: async ({ pageParam }) => {
      const { data } = await listInvitationsApiV1OrgsOrgIdInvitationsGet({
        path: { org_id: orgId },
        query: { after: pageParam, limit: 20 },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
  });

  const invitations =
    invitationsQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  // fetchNextPage is referentially stable in react-query v5, so pulling it
  // out keeps this callback stable too -- VirtualList takes onLoadMore as an
  // effect dependency.
  const { fetchNextPage } = invitationsQuery;
  const loadMore = useCallback(() => {
    void fetchNextPage();
  }, [fetchNextPage]);

  return (
    <SettingsSection
      title={t("tenancy.invitations.title")}
      description={t("tenancy.invitations.description")}
    >
      <div className="space-y-4">
        {invitationsQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : invitationsQuery.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {getErrorMessage(
                invitationsQuery.error,
                "tenancy.invitations.error_load",
              )}
            </AlertDescription>
          </Alert>
        ) : invitations.length === 0 ? (
          <p
            className="text-sm text-muted-foreground"
            data-testid="no-pending-invitations"
          >
            {t("tenancy.invitations.none")}
          </p>
        ) : (
          <div className="@container/list">
            {/* Outside the scroll container for the same reason as the
                member list's: a virtualized list has no <thead> to pin. */}
            <DataListColumns className={INVITATION_COLUMNS}>
              <span>{t("tenancy.invitations.email_col")}</span>
              <span>{t("tenancy.invitations.role_col")}</span>
              <span className="text-end">
                {t("tenancy.invitations.expires_col")}
              </span>
              <span />
            </DataListColumns>
            <VirtualList
              items={invitations}
              getItemKey={(invitation) => invitation.id}
              label={t("tenancy.invitations.list_label")}
              estimateSize={ESTIMATED_ROW_HEIGHT_PX}
              maxHeight={MAX_LIST_HEIGHT_PX}
              hasMore={invitationsQuery.hasNextPage}
              isLoadingMore={invitationsQuery.isFetchingNextPage}
              onLoadMore={loadMore}
              itemTestId="invitation-row"
              scrollRestorationId={INVITATIONS_LIST_SCROLL_ID}
              {...(initialScrollOffset === undefined
                ? {}
                : { initialOffset: initialScrollOffset })}
              renderItem={(invitation) => (
                <DataListRow className={INVITATION_COLUMNS}>
                  <span className="truncate" title={invitation.email}>
                    {invitation.email}
                  </span>
                  {/* Reuses the member list's role labels rather than
                      duplicating three keys under invitations.* -- an
                      invited "admin" is the same word as a member one. */}
                  <span>{t(`tenancy.members.role.${invitation.role}`)}</span>
                  <span className="text-end">
                    {/* An expired-but-unaccepted invitation stays in this
                        list by design (the backend filters on accepted_at
                        only) -- it is the row an admin needs in order to
                        re-send, so it is labelled, not hidden. */}
                    {isExpired(invitation) ? (
                      <StatusPill tone="danger">
                        {t("tenancy.invitations.expired")}
                      </StatusPill>
                    ) : (
                      <span className="whitespace-nowrap text-muted-foreground">
                        {t("tenancy.invitations.expires", {
                          date: formatDate(
                            invitation.expires_at,
                            i18n.language,
                          ),
                        })}
                      </span>
                    )}
                  </span>
                  <span className="text-end">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <RowActionsButton
                          aria-label={t("tenancy.invitations.actions_label", {
                            email: invitation.email,
                          })}
                          data-testid="invitation-actions"
                        />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-48">
                        <DropdownMenuItem
                          disabled={resend.isPending}
                          onSelect={() => {
                            resend.mutate(invitation);
                          }}
                        >
                          {t("tenancy.invitations.resend")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-destructive focus:text-destructive"
                          onSelect={() => {
                            setPendingRevoke(invitation);
                          }}
                        >
                          {t("tenancy.invitations.revoke")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </span>
                </DataListRow>
              )}
            />
          </div>
        )}

        {/* The non-scroll path to the next page -- same rationale as the
            member list's button. */}
        {invitationsQuery.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            onClick={loadMore}
            disabled={invitationsQuery.isFetchingNextPage}
          >
            {t("tenancy.invitations.load_more")}
          </Button>
        ) : null}
      </div>

      <ConfirmDialog
        open={pendingRevoke !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingRevoke(null);
          }
        }}
        title={t("tenancy.invitations.revoke_confirm_title")}
        description={t("tenancy.invitations.revoke_confirm_body", {
          email: pendingRevoke?.email ?? "",
        })}
        confirmLabel={t("tenancy.invitations.revoke")}
        destructive
        isPending={revoke.isPending}
        onConfirm={() => {
          const invitation = pendingRevoke;
          if (!invitation) {
            return;
          }
          // Closed before the request, as FilesPage does: the answer is given.
          setPendingRevoke(null);
          revoke.mutate(invitation);
        }}
      />
    </SettingsSection>
  );
}
