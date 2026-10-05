import { useEffect, useId, useState } from "react";
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
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Page,
  PageHeader,
  SettingsRow,
  SettingsSection,
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
  const formId = useId();
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

  const header = (
    <PageHeader
      title={t("tenancy.org_settings.title")}
      description={t("tenancy.org_settings.description")}
    />
  );

  if (isLoading || !org) {
    return (
      <Page>
        {header}
        <Skeleton className="h-32 w-full" />
      </Page>
    );
  }

  return (
    <Page>
      {header}
      <SettingsSection
        title={t("tenancy.org_settings.section_general")}
        footer={
          canManage ? (
            <Button
              type="submit"
              form={formId}
              disabled={form.formState.isSubmitting || !form.formState.isDirty}
            >
              {t("tenancy.org_settings.save")}
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("tenancy.org_settings.permission_notice")}
            </p>
          )
        }
      >
        {formError ? (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}
        {saved ? (
          <Alert className="mb-4">
            <AlertDescription>
              {t("tenancy.org_settings.saved")}
            </AlertDescription>
          </Alert>
        ) : null}

        <SettingsRow
          label={t("tenancy.org_settings.logo_label")}
          hint={t("tenancy.org_settings.coming_soon")}
        >
          <Avatar className="size-12" data-testid="org-avatar">
            <AvatarFallback>
              {org.name.slice(0, 2).toUpperCase()}
            </AvatarFallback>
          </Avatar>
          {/* No storage upload endpoint exists yet -- same stub as before. */}
          <Button type="button" variant="outline" disabled>
            {t("tenancy.org_settings.logo_upload")}
          </Button>
        </SettingsRow>

        <Form {...form}>
          <form
            id={formId}
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
                  <SettingsRow
                    label={
                      <FormLabel>
                        {t("tenancy.org_settings.name_label")}
                      </FormLabel>
                    }
                    hint={t("tenancy.org_settings.name_hint")}
                  >
                    <div className="w-full max-w-sm space-y-2">
                      <FormControl>
                        <Input disabled={!canManage} {...field} />
                      </FormControl>
                      <FormMessage />
                    </div>
                  </SettingsRow>
                </FormItem>
              )}
            />
          </form>
        </Form>
      </SettingsSection>
    </Page>
  );
}
