import { useEffect, useId, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import {
  leaveOrgApiV1OrgsOrgIdLeavePost,
  updateOrgApiV1OrgsOrgIdPatch,
} from "@forge/api-client";
import { useModules, useOrg, usePermission } from "@forge/hooks";
import { Permission } from "@forge/types";
import {
  Alert,
  AlertDescription,
  Avatar,
  AvatarFallback,
  AvatarImage,
  Button,
  ConfirmDialog,
  FileUploader,
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
  toast,
} from "@forge/ui";

import { orgLogoQueryKey, useOrgLogoSrc } from "../../lib/useOrgLogoSrc";
import { useRouteLocale } from "../../lib/useRouteLocale";
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
  const modules = useModules();
  const queryClient = useQueryClient();
  const logoSrc = useOrgLogoSrc(org);
  const setLogo = useMutation({
    // null clears the logo. The id, never a storage key: the server checks
    // the file is in this org's logos bucket.
    mutationFn: async (input: { orgId: string; fileId: string | null }) => {
      await updateOrgApiV1OrgsOrgIdPatch({
        path: { org_id: input.orgId },
        body: { logo_file_id: input.fileId },
        throwOnError: true,
      });
    },
  });
  const navigate = useNavigate();
  const locale = useRouteLocale();
  const [confirmLeave, setConfirmLeave] = useState(false);
  const leaveOrg = useMutation({
    mutationFn: async (orgId: string) => {
      await leaveOrgApiV1OrgsOrgIdLeavePost({
        path: { org_id: orgId },
        throwOnError: true,
      });
    },
  });
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

  const onLogoChanged = async (fileId: string | null): Promise<void> => {
    if (!org) return;
    try {
      await setLogo.mutateAsync({ orgId: org.id, fileId });
      // has_uploaded_logo lives on the org, so the switcher learns about it
      // from a refresh; the image itself from the shared logo query.
      await refreshOrgs();
      await queryClient.invalidateQueries({
        queryKey: orgLogoQueryKey(org.id),
      });
      toast.success(
        t(
          fileId === null
            ? "tenancy.org_settings.logo_removed"
            : "tenancy.org_settings.logo_saved",
        ),
      );
    } catch (error) {
      toast.error(getErrorMessage(error, "tenancy.org_settings.error_logo"));
    }
  };

  const onLeave = async (): Promise<void> => {
    if (!org) return;
    const name = org.name;
    try {
      await leaveOrg.mutateAsync(org.id);
      toast.success(t("tenancy.org_settings.left", { org: name }));
      // The switcher picks the next organization (or the create-your-first
      // screen if there is none); the dashboard is where to land either way.
      await refreshOrgs();
      await navigate({ to: "/$locale", params: { locale } });
    } catch (error) {
      // The last owner is refused with a message that says what to do.
      toast.error(getErrorMessage(error, "tenancy.org_settings.error_leave"));
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
          {...(modules.fileStorage
            ? { hint: t("tenancy.org_settings.logo_hint") }
            : {})}
        >
          <Avatar className="size-12 rounded-lg" data-testid="org-avatar">
            {logoSrc ? <AvatarImage src={logoSrc} alt="" /> : null}
            <AvatarFallback className="rounded-lg">
              {org.name.slice(0, 2).toUpperCase()}
            </AvatarFallback>
          </Avatar>
          {/* Uploading needs somewhere to put the file: with file storage off
              the initials are the logo, and there is nothing to offer. */}
          {canManage && modules.fileStorage ? (
            <>
              <FileUploader
                variant="button"
                buttonLabel={t("tenancy.org_settings.logo_upload")}
                bucket="logos"
                accept="image/png,image/jpeg,image/webp"
                maxSize={2 * 1024 * 1024}
                onUploaded={(file) => {
                  void onLogoChanged(file.id);
                }}
              />
              {org.has_uploaded_logo ? (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={setLogo.isPending}
                  onClick={() => {
                    void onLogoChanged(null);
                  }}
                  data-testid="org-logo-remove"
                >
                  {t("tenancy.org_settings.logo_remove")}
                </Button>
              ) : null}
            </>
          ) : null}
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

      <SettingsSection title={t("tenancy.org_settings.section_leave")}>
        <SettingsRow
          label={t("tenancy.org_settings.leave_label", { org: org.name })}
          hint={t("tenancy.org_settings.leave_hint")}
        >
          <Button
            type="button"
            variant="outline"
            className="text-destructive hover:text-destructive"
            disabled={leaveOrg.isPending}
            onClick={() => {
              setConfirmLeave(true);
            }}
            data-testid="org-leave"
          >
            {t("tenancy.org_settings.leave_button")}
          </Button>
        </SettingsRow>
      </SettingsSection>

      <ConfirmDialog
        open={confirmLeave}
        onOpenChange={setConfirmLeave}
        title={t("tenancy.org_settings.leave_confirm_title", { org: org.name })}
        description={t("tenancy.org_settings.leave_hint")}
        confirmLabel={t("tenancy.org_settings.leave_button")}
        destructive
        isPending={leaveOrg.isPending}
        onConfirm={() => {
          setConfirmLeave(false);
          void onLeave();
        }}
      />
    </Page>
  );
}
