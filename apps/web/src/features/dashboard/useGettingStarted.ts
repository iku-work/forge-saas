import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  getSubscriptionApiV1BillingSubscriptionGet,
  getSubscriptionApiV1BillingSubscriptionGetQueryKey,
  listFilesApiV1FilesGet,
  listFilesApiV1FilesGetInfiniteQueryKey,
  listInvitationsApiV1OrgsOrgIdInvitationsGet,
  listInvitationsApiV1OrgsOrgIdInvitationsGetInfiniteQueryKey,
  listMembersApiV1OrgsOrgIdMembersGet,
  listMembersApiV1OrgsOrgIdMembersGetInfiniteQueryKey,
} from "@forge/api-client";
import { useAuth, useModules, useOrg, usePermission } from "@forge/hooks";
import { Permission } from "@forge/types";

/**
 * The dashboard's first-run checklist (visual overhaul, ticket 10).
 *
 * Every "done" comes from data the app already fetches, through the *same*
 * query keys and shapes the real pages use, so finishing a step on its own
 * page updates this list without a refetch and no backend state is needed.
 * A step only appears when its module is on and the member may do it -- a
 * step you cannot complete is worse than no step.
 */

export interface Step {
  id: "profile" | "files" | "invite" | "plan";
  done: boolean;
  title: string;
  hint: string;
  actionLabel: string;
  to:
    | "/$locale/settings/profile"
    | "/$locale/files"
    | "/$locale/settings/members"
    | "/$locale/settings/billing";
}

export interface GettingStartedState {
  isLoading: boolean;
  steps: Step[];
}

/** Which steps apply to this member, and which are done. */
export function useGettingStarted(): GettingStartedState {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { org } = useOrg();
  const modules = useModules();
  const canInvite = usePermission(Permission.MEMBERS_MANAGE);
  const canChoosePlan = usePermission(Permission.BILLING_MANAGE);
  const orgId = org?.id ?? "";

  // One page is enough everywhere below: "at least one" never needs more.
  const filesQuery = useInfiniteQuery({
    queryKey: listFilesApiV1FilesGetInfiniteQueryKey(),
    queryFn: async ({ pageParam }) => {
      const { data } = await listFilesApiV1FilesGet({
        query: { after: pageParam, limit: 20 },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
    enabled: Boolean(org) && modules.fileStorage,
  });
  const membersQuery = useInfiniteQuery({
    queryKey: listMembersApiV1OrgsOrgIdMembersGetInfiniteQueryKey({
      path: { org_id: orgId },
    }),
    queryFn: async ({ pageParam }) => {
      const { data } = await listMembersApiV1OrgsOrgIdMembersGet({
        path: { org_id: orgId },
        query: { after: pageParam, limit: 20 },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
    enabled: Boolean(org) && canInvite,
  });
  // Invitations are listed only for members who may manage them (the
  // endpoint answers 403 otherwise), which is also who sees this step.
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
    enabled: Boolean(org) && canInvite,
  });
  const subscriptionQuery = useQuery({
    queryKey: getSubscriptionApiV1BillingSubscriptionGetQueryKey(),
    queryFn: async () => {
      const { data } = await getSubscriptionApiV1BillingSubscriptionGet({
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org) && modules.billing && canChoosePlan,
  });

  const count = (
    query: typeof membersQuery | typeof filesQuery | typeof invitationsQuery,
  ): number => query.data?.pages[0]?.data?.length ?? 0;

  const steps: Step[] = [];
  steps.push({
    id: "profile",
    // With file storage on, a photo is part of a finished profile; without
    // it there is nowhere to upload one, so the name alone counts.
    done:
      Boolean(user?.full_name) &&
      (!modules.fileStorage || user?.has_uploaded_avatar === true),
    title: t("common.dashboard.step_profile_title"),
    hint: modules.fileStorage
      ? t("common.dashboard.step_profile_hint")
      : t("common.dashboard.step_profile_hint_no_photo"),
    actionLabel: t("common.dashboard.step_profile_action"),
    to: "/$locale/settings/profile",
  });
  if (modules.fileStorage) {
    steps.push({
      id: "files",
      done: count(filesQuery) > 0,
      title: t("common.dashboard.step_files_title"),
      hint: t("common.dashboard.step_files_hint"),
      actionLabel: t("common.dashboard.step_files_action"),
      to: "/$locale/files",
    });
  }
  if (canInvite) {
    steps.push({
      id: "invite",
      done: count(membersQuery) > 1 || count(invitationsQuery) > 0,
      title: t("common.dashboard.step_invite_title"),
      hint: t("common.dashboard.step_invite_hint"),
      actionLabel: t("common.dashboard.step_invite_action"),
      to: "/$locale/settings/members",
    });
  }
  if (modules.billing && canChoosePlan) {
    const subscription = subscriptionQuery.data?.data ?? null;
    steps.push({
      id: "plan",
      // A paid plan in use: a subscription that is active, not a trial.
      done: subscription?.status === "active",
      title: t("common.dashboard.step_plan_title"),
      hint: t("common.dashboard.step_plan_hint"),
      actionLabel: t("common.dashboard.step_plan_action"),
      to: "/$locale/settings/billing",
    });
  }

  const isLoading =
    !org ||
    (modules.fileStorage && filesQuery.isLoading) ||
    (canInvite && (membersQuery.isLoading || invitationsQuery.isLoading)) ||
    (modules.billing && canChoosePlan && subscriptionQuery.isLoading);

  return { isLoading, steps };
}
