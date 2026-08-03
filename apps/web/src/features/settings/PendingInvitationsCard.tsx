import { useCallback } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  listInvitationsApiV1OrgsOrgIdInvitationsGet,
  listInvitationsApiV1OrgsOrgIdInvitationsGetInfiniteQueryKey,
} from "@forge/api-client";
import type { InvitationPublic } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { VirtualList } from "../../components/VirtualList";
import { INVITATIONS_LIST_SCROLL_ID } from "../../lib/scrollRestorationIds";
import { getErrorMessage } from "../auth/getErrorMessage";

// Same fixed-height row + capped scroll port as the member list above it
// (see MembersSettingsPage), so the two lists on this page scroll and
// paginate identically.
const ESTIMATED_ROW_HEIGHT_PX = 52;
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
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {t("tenancy.invitations.title")}
        </CardTitle>
        <CardDescription>{t("tenancy.invitations.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
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
          <div>
            {/* Outside the scroll container for the same reason as the
                member list's: a virtualized list has no <thead> to pin. */}
            <div
              aria-hidden="true"
              className="flex items-center gap-4 border-b px-3 pb-2 text-xs font-medium text-muted-foreground"
            >
              <span className="flex-1">
                {t("tenancy.invitations.email_col")}
              </span>
              <span className="w-28">{t("tenancy.invitations.role_col")}</span>
              <span className="w-36 text-end">
                {t("tenancy.invitations.expires_col")}
              </span>
            </div>
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
                <div className="flex items-center gap-4 border-b px-3 py-3 text-sm">
                  <span className="flex-1 truncate" title={invitation.email}>
                    {invitation.email}
                  </span>
                  <span className="w-28">
                    <Badge variant="secondary">
                      {/* Reuses the member list's role labels rather than
                          duplicating three keys under invitations.* -- an
                          invited "admin" is the same word as a member one. */}
                      {t(`tenancy.members.role.${invitation.role}`)}
                    </Badge>
                  </span>
                  <span className="w-36 text-end">
                    {/* An expired-but-unaccepted invitation stays in this
                        list by design (the backend filters on accepted_at
                        only) -- it is the row an admin needs in order to
                        re-send, so it is labelled, not hidden. */}
                    {isExpired(invitation) ? (
                      <Badge variant="destructive">
                        {t("tenancy.invitations.expired")}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground">
                        {t("tenancy.invitations.expires", {
                          date: formatDate(invitation.expires_at, i18n.language),
                        })}
                      </span>
                    )}
                  </span>
                </div>
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
      </CardContent>
    </Card>
  );
}
