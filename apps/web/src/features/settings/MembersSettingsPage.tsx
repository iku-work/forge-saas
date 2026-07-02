import { useState } from "react";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

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
                <Input type="email" autoComplete="email" {...field} />
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
        >
          {form.formState.isSubmitting
            ? t("tenancy.invite.submit_pending")
            : t("tenancy.invite.submit")}
        </Button>
      </form>
    </Form>
  );
}

export function MembersSettingsPage() {
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
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("tenancy.members.member_col")}</TableHead>
                  <TableHead>{t("tenancy.members.role_col")}</TableHead>
                  {canManage ? (
                    <TableHead className="text-right">
                      {t("tenancy.members.action_col")}
                    </TableHead>
                  ) : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell>
                      {member.user_id === user?.id ? (
                        t("tenancy.members.you")
                      ) : (
                        <span className="font-mono text-xs">
                          {member.user_id.slice(0, 8)}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
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
                    </TableCell>
                    {canManage ? (
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            void handleRemove(member.id);
                          }}
                        >
                          {t("tenancy.members.remove")}
                        </Button>
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {membersQuery.hasNextPage ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void membersQuery.fetchNextPage();
              }}
              disabled={membersQuery.isFetchingNextPage}
            >
              {t("tenancy.members.load_more")}
            </Button>
          ) : null}
        </CardContent>
      </Card>

      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenancy.invite.title")}</DialogTitle>
          </DialogHeader>
          <InviteForm
            orgId={org.id}
            onInvited={() => {
              setInviteOpen(false);
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
