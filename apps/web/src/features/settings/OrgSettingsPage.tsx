import { useEffect, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { updateOrgApiV1OrgsOrgIdPatch } from "@forge/api-client";
import { useOrg, usePermission } from "@forge/hooks";
import { Permission } from "@forge/types";
import {
  Alert,
  AlertDescription,
  Avatar,
  AvatarFallback,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Skeleton,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

const updateOrgSchema = z.object({
  name: z.string().min(1, "validation.org_name_required").max(255),
});
type UpdateOrgInput = z.infer<typeof updateOrgSchema>;

export function OrgSettingsPage() {
  const { t } = useTranslation();
  const { org, isLoading, refreshOrgs } = useOrg();
  const canManage = usePermission(Permission.SETTINGS_MANAGE);
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // Raw SDK call rather than the generated *Mutation() helper -- see
  // CreateOrgForm.tsx for why (the helper's internal call is unmockable
  // once @forge/api-client is bundled).
  const updateOrg = useMutation({
    mutationFn: async (input: { orgId: string; name: string }) => {
      const { data } = await updateOrgApiV1OrgsOrgIdPatch({
        path: { org_id: input.orgId },
        body: { name: input.name },
        throwOnError: true,
      });
      return data;
    },
  });

  const form = useForm<UpdateOrgInput>({
    resolver: zodResolver(updateOrgSchema),
    defaultValues: { name: org?.name ?? "" },
  });

  // org loads asynchronously after this page mounts, so the form's initial
  // defaultValues (set before that resolves) needs to be re-synced once the
  // real name is known -- and again on every switchOrg. react-hook-form's
  // returned object is stable across renders, so including it doesn't
  // trigger extra reset calls beyond org actually changing.
  useEffect(() => {
    form.reset({ name: org?.name ?? "" });
  }, [org?.id, org?.name, form]);

  const onSubmit = async (values: UpdateOrgInput): Promise<void> => {
    if (!org) return;
    setFormError(null);
    setSaved(false);
    try {
      await updateOrg.mutateAsync({ orgId: org.id, name: values.name });
      await refreshOrgs();
      setSaved(true);
    } catch (error) {
      setFormError(
        getErrorMessage(error, "tenancy.org_settings.error_default"),
      );
    }
  };

  if (isLoading || !org) {
    return (
      <div className="max-w-xl space-y-6 p-6">
        <h1 className="text-xl font-semibold">
          {t("tenancy.org_settings.title")}
        </h1>
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return (
    <div className="max-w-xl space-y-6 p-6">
      <h1 className="text-xl font-semibold">
        {t("tenancy.org_settings.title")}
      </h1>
      <Card>
        <CardHeader>
          <CardDescription>
            {t("tenancy.org_settings.description")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-center gap-4">
            <Avatar className="size-16" data-testid="org-avatar">
              <AvatarFallback className="text-lg">
                {org.name.slice(0, 2).toUpperCase()}
              </AvatarFallback>
            </Avatar>
            <div>
              {/* No storage upload endpoint exists yet -- same stub as the
                  Profile page's avatar upload. */}
              <Button type="button" variant="outline" size="sm" disabled>
                {t("tenancy.org_settings.logo_upload")}
              </Button>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("tenancy.org_settings.coming_soon")}
              </p>
            </div>
          </div>

          {formError ? (
            <Alert variant="destructive">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}
          {saved ? (
            <Alert>
              <AlertDescription>
                {t("tenancy.org_settings.saved")}
              </AlertDescription>
            </Alert>
          ) : null}

          <Form {...form}>
            <form
              className="space-y-4"
              onSubmit={(event) => {
                void form.handleSubmit(onSubmit)(event);
              }}
              noValidate
            >
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {t("tenancy.org_settings.name_label")}
                    </FormLabel>
                    <FormControl>
                      <Input disabled={!canManage} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {canManage ? (
                <Button type="submit" disabled={form.formState.isSubmitting}>
                  {t("tenancy.org_settings.save")}
                </Button>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("tenancy.org_settings.permission_notice")}
                </p>
              )}
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
}
