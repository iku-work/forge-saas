import { useCallback, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import {
  createInvitationApiV1OrgsOrgIdInvitationsPost,
  listInvitationsApiV1OrgsOrgIdInvitationsGetInfiniteQueryKey,
  listMembersApiV1OrgsOrgIdMembersGet,
  listMembersApiV1OrgsOrgIdMembersGetInfiniteQueryKey,
  removeMemberApiV1OrgsOrgIdMembersMembershipIdDelete,
  updateMemberRoleApiV1OrgsOrgIdMembersMembershipIdPatch,
} from "@forge/api-client";
import type { MemberRole as ApiMemberRole } from "@forge/api-client";
import { useAuth, useOrg, usePermission } from "@forge/hooks";
import { MemberRole, Permission } from "@forge/types";
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Skeleton,
} from "@forge/ui";

import { VirtualList } from "../../components/VirtualList";
import { MEMBERS_LIST_SCROLL_ID } from "../../lib/scrollRestorationIds";
import { getErrorMessage } from "../auth/getErrorMessage";
import { PendingInvitationsCard } from "./PendingInvitationsCard";

// The member row is a fixed-height flex row, so one estimate covers every
// row and the virtualizer never has to re-measure after mount. MAX_HEIGHT
// is what makes the container a scroll port at all -- without it the list
// grows to its full height and there is nothing to virtualize against.
const ESTIMATED_ROW_HEIGHT_PX = 52;
const MAX_LIST_HEIGHT_PX = 420;

const ROLE_OPTIONS: ApiMemberRole[] = [
  MemberRole.OWNER,
  MemberRole.ADMIN,
  MemberRole.MEMBER,
];

const inviteSchema = z.object({
  email: z.string().email("validation.email_invalid"),
  role: z.enum(["owner", "admin", "member"], {
    message: "validation.role_required",
  }),
});
type InviteInput = z.infer<typeof inviteSchema>;

function roleLabelKey(role: ApiMemberRole): string {
  return `tenancy.members.role.${role}`;
}

function InviteForm({
  orgId,
  onInvited,
}: {
  orgId: string;
  onInvited: () => void;
}) {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  // Raw SDK call rather than the generated *Mutation() helper -- see
  // CreateOrgForm.tsx for why.
  const createInvitation = useMutation({
    mutationFn: async (input: InviteInput) => {
      await createInvitationApiV1OrgsOrgIdInvitationsPost({
        path: { org_id: orgId },
        body: input,
        throwOnError: true,
      });
    },
  });

  const form = useForm<InviteInput>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { email: "", role: "member" },
  });

  const onSubmit = async (values: InviteInput): Promise<void> => {
    setFormError(null);
    try {
      await createInvitation.mutateAsync(values);
      form.reset();
      onInvited();
    } catch (error) {
      setFormError(getErrorMessage(error, "tenancy.invite.error_default"));
    }
  };

  return (
    <Form {...form}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          void form.handleSubmit(onSubmit)(event);
        }}
        noValidate
      >
        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.invite.email_label")}</FormLabel>
              <FormControl>
                <Input
                  type="email"
                  autoComplete="email"
                  data-testid="invite-email-input"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="role"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.invite.role_label")}</FormLabel>
              <FormControl>
                <select
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm"
                  data-testid="invite-role-select"
                  {...field}
                >
                  {ROLE_OPTIONS.map((role) => (
                    <option key={role} value={role}>
                      {t(roleLabelKey(role))}
                    </option>
                  ))}
                </select>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button
          type="submit"
          className="w-full"
          disabled={form.formState.isSubmitting}
          data-testid="invite-submit"
        >
          {form.formState.isSubmitting
            ? t("tenancy.invite.submit_pending")
            : t("tenancy.invite.submit")}
        </Button>
      </form>
    </Form>
  );
}

export interface MembersSettingsPageProps {
  /** Scroll offset restored by TanStack Router for this org's member list
   *  (T-241). Supplied by the route, which is the only place that can call
   *  useElementScrollRestoration -- see routes/$locale._app.settings.members.tsx. */
  initialScrollOffset?: number;
  /** The same, for the pending-invitations list below it. */
  initialInvitationsScrollOffset?: number;
}

export function MembersSettingsPage({
  initialScrollOffset,
  initialInvitationsScrollOffset,
}: MembersSettingsPageProps = {}) {
  const { t } = useTranslation();
  const { org, isLoading: orgLoading } = useOrg();
  const { user } = useAuth();
  const canManage = usePermission(Permission.MEMBERS_MANAGE);
  const queryClient = useQueryClient();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Raw SDK call rather than the generated *GetInfiniteOptions() helper --
  // see CreateOrgForm.tsx for why. The query key function alone is fine to
  // reuse since it doesn't make a network call.
  const membersQuery = useInfiniteQuery({
    queryKey: listMembersApiV1OrgsOrgIdMembersGetInfiniteQueryKey({
      path: { org_id: org?.id ?? "" },
    }),
    queryFn: async ({ pageParam }) => {
      const { data } = await listMembersApiV1OrgsOrgIdMembersGet({
        path: { org_id: org?.id ?? "" },
        query: { after: pageParam, limit: 20 },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
    enabled: Boolean(org),
  });

  const updateRole = useMutation({
    mutationFn: async (input: {
      orgId: string;
      membershipId: string;
      role: ApiMemberRole;
    }) => {
      const { data } =
        await updateMemberRoleApiV1OrgsOrgIdMembersMembershipIdPatch({
          path: { org_id: input.orgId, membership_id: input.membershipId },
          body: { role: input.role },
          throwOnError: true,
        });
      return data;
    },
  });
  const removeMember = useMutation({
    mutationFn: async (input: { orgId: string; membershipId: string }) => {
      await removeMemberApiV1OrgsOrgIdMembersMembershipIdDelete({
        path: { org_id: input.orgId, membership_id: input.membershipId },
        throwOnError: true,
      });
    },
  });

  const invalidateMembers = async (): Promise<void> => {
    if (!org) return;
    await queryClient.invalidateQueries({
      queryKey: listMembersApiV1OrgsOrgIdMembersGetInfiniteQueryKey({
        path: { org_id: org.id },
      }),
    });
  };

  // A sent invitation is a new row in the pending list below, so the invite
  // dialog closing has to bust that query too -- otherwise the teammate you
  // just invited doesn't appear until the next navigation.
  const invalidateInvitations = async (): Promise<void> => {
    if (!org) return;
    await queryClient.invalidateQueries({
      queryKey: listInvitationsApiV1OrgsOrgIdInvitationsGetInfiniteQueryKey({
        path: { org_id: org.id },
      }),
    });
  };

  const handleRoleChange = async (
    membershipId: string,
    role: ApiMemberRole,
  ): Promise<void> => {
    if (!org) return;
    setActionError(null);
    try {
      await updateRole.mutateAsync({ orgId: org.id, membershipId, role });
      await invalidateMembers();
    } catch (error) {
      setActionError(
        getErrorMessage(error, "tenancy.members.error_role_change"),
      );
    }
  };

  const handleRemove = async (membershipId: string): Promise<void> => {
    if (!org) return;
    setActionError(null);
    try {
      await removeMember.mutateAsync({ orgId: org.id, membershipId });
      await invalidateMembers();
    } catch (error) {
      setActionError(getErrorMessage(error, "tenancy.members.error_remove"));
    }
  };

  const members =
    membersQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  // fetchNextPage is referentially stable in react-query v5, so pulling it
  // out keeps this callback stable too -- VirtualList takes onLoadMore as
  // an effect dependency.
  const { fetchNextPage } = membersQuery;
  const loadMoreMembers = useCallback(() => {
    void fetchNextPage();
  }, [fetchNextPage]);

  if (orgLoading || !org) {
    return (
      <div className="max-w-3xl space-y-6 p-6">
        <h1 className="text-xl font-semibold">{t("tenancy.members.title")}</h1>
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return (
    <div className="max-w-3xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t("tenancy.members.title")}</h1>
        {canManage ? (
          <Button
            type="button"
            onClick={() => {
              setInviteOpen(true);
            }}
            data-testid="members-invite-button"
          >
            {t("tenancy.members.invite_button")}
          </Button>
        ) : null}
      </div>

      <Card>
        <CardHeader>
          <CardDescription>{t("tenancy.members.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {actionError ? (
            <Alert variant="destructive">
              <AlertDescription>{actionError}</AlertDescription>
            </Alert>
          ) : null}

          {membersQuery.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : membersQuery.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {getErrorMessage(
                  membersQuery.error,
                  "tenancy.members.error_load",
                )}
              </AlertDescription>
            </Alert>
          ) : members.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("tenancy.members.no_members")}
            </p>
          ) : (
            <div>
              {/* The column labels live outside the scroll container: a
                  virtualized list has no <thead> to pin, and putting them
                  inside would scroll them away with the rows. */}
              <div
                aria-hidden="true"
                className="flex items-center gap-4 border-b px-3 pb-2 text-xs font-medium text-muted-foreground"
              >
                <span className="flex-1">
                  {t("tenancy.members.member_col")}
                </span>
                <span className="w-28">{t("tenancy.members.role_col")}</span>
                {canManage ? (
                  <span className="w-24 text-end">
                    {t("tenancy.members.action_col")}
                  </span>
                ) : null}
              </div>
              <VirtualList
                items={members}
                getItemKey={(member) => member.id}
                label={t("tenancy.members.list_label")}
                estimateSize={ESTIMATED_ROW_HEIGHT_PX}
                maxHeight={MAX_LIST_HEIGHT_PX}
                hasMore={membersQuery.hasNextPage}
                isLoadingMore={membersQuery.isFetchingNextPage}
                onLoadMore={loadMoreMembers}
                itemTestId="member-row"
                scrollRestorationId={MEMBERS_LIST_SCROLL_ID}
                {...(initialScrollOffset === undefined
                  ? {}
                  : { initialOffset: initialScrollOffset })}
                renderItem={(member) => (
                  <div className="flex items-center gap-4 border-b px-3 py-3 text-sm">
                    <span className="flex-1">
                      {member.user_id === user?.id ? (
                        t("tenancy.members.you")
                      ) : (
                        <span className="font-mono text-xs">
                          {member.user_id.slice(0, 8)}
                        </span>
                      )}
                    </span>
                    <span className="w-28">
                      {canManage ? (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button
                              type="button"
                              aria-label={t(
                                "tenancy.members.change_role_label",
                                {
                                  user_id: member.user_id,
                                },
                              )}
                            >
                              <Badge variant="secondary">
                                {t(roleLabelKey(member.role))}
                              </Badge>
                            </button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="start">
                            {ROLE_OPTIONS.map((role) => (
                              <DropdownMenuItem
                                key={role}
                                onSelect={() => {
                                  void handleRoleChange(member.id, role);
                                }}
                              >
                                {t(roleLabelKey(role))}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : (
                        <Badge variant="secondary">
                          {t(roleLabelKey(member.role))}
                        </Badge>
                      )}
                    </span>
                    {canManage ? (
                      <span className="w-24 text-end">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={t("tenancy.members.remove_label", {
                            user_id: member.user_id,
                          })}
                          onClick={() => {
                            void handleRemove(member.id);
                          }}
                        >
                          {t("tenancy.members.remove")}
                        </Button>
                      </span>
                    ) : null}
                  </div>
                )}
              />
            </div>
          )}

          {/* Scrolling is the pagination driver (VirtualList fetches the
              next cursor page as the rendered range nears the loaded
              count); this button is the non-scroll path -- keyboard users
              tabbing past the list, and any environment where the scroll
              container never reports a scroll event. */}
          {membersQuery.hasNextPage ? (
            <Button
              type="button"
              variant="outline"
              onClick={loadMoreMembers}
              disabled={membersQuery.isFetchingNextPage}
            >
              {t("tenancy.members.load_more")}
            </Button>
          ) : null}
        </CardContent>
      </Card>

      {/* MEMBERS_MANAGE only: GET /orgs/{id}/invitations is gated on it (the
          response is a list of invitee email addresses), so rendering this
          for a plain member would produce a 403 error box, not a feature. */}
      {canManage ? (
        <PendingInvitationsCard
          orgId={org.id}
          {...(initialInvitationsScrollOffset === undefined
            ? {}
            : { initialScrollOffset: initialInvitationsScrollOffset })}
        />
      ) : null}

      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenancy.invite.title")}</DialogTitle>
          </DialogHeader>
          <InviteForm
            orgId={org.id}
            onInvited={() => {
              setInviteOpen(false);
              void invalidateInvitations();
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
