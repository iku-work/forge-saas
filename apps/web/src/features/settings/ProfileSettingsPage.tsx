import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId } from "react";
import { Link } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { z } from "zod";

import { updateMeApiV1AuthMePatch } from "@forge/api-client";
import { useAuth } from "@forge/hooks";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
  Button,
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
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";
import { ThemeToggle } from "../../components/ThemeToggle";
import { RoutedLocaleSwitcher } from "../../lib/RoutedLocaleSwitcher";
import { AVATAR_QUERY_KEY, useAvatarSrc } from "../../lib/useAvatarSrc";
import { useRouteLocale } from "../../lib/useRouteLocale";

function initialsFor(email: string): string {
  return email.slice(0, 2).toUpperCase();
}

// Matches UpdateMeRequest's cap on the server (auth/schemas.py's
// _FULL_NAME_MAX_LENGTH). Empty is allowed and means "clear it" -- the
// backend maps "" and whitespace to null, so a user who wants no name has a
// way to say so.
const profileSchema = z.object({
  fullName: z.string().max(200, "validation.name_too_long"),
});
type ProfileInput = z.infer<typeof profileSchema>;

export function ProfileSettingsPage() {
  const { t } = useTranslation();
  const { user, isLoading, applyUpdatedUser } = useAuth();
  const locale = useRouteLocale();
  const detailsFormId = useId();

  const form = useForm<ProfileInput>({
    resolver: zodResolver(profileSchema),
    defaultValues: { fullName: "" },
  });

  // `user` arrives after the first render (isLoading above), so the form has
  // to be seeded when it does rather than only via defaultValues. Keyed on
  // the value itself so a change made elsewhere -- the admin directory, say
  // -- is picked up, while `reset` keeps the field pristine so an untouched
  // form is not reported as dirty.
  const fullName = user?.full_name ?? "";
  useEffect(() => {
    form.reset({ fullName });
  }, [form, fullName]);

  const avatarSrc = useAvatarSrc();
  const queryClient = useQueryClient();

  const setAvatar = useMutation({
    // null clears the photo: a write to the user row and nothing else, so
    // it works with file storage off too.
    mutationFn: async (fileId: string | null) => {
      const { data } = await updateMeApiV1AuthMePatch({
        // The id, never the storage key: the server resolves it and checks
        // the file is this user's, which is what keeps a private bucket
        // private.
        body: { avatar_file_id: fileId },
        throwOnError: true,
      });
      return data;
    },
  });

  const updateProfile = useMutation({
    mutationFn: async (input: ProfileInput) => {
      const { data } = await updateMeApiV1AuthMePatch({
        // null, not "": both are "no name" to a person, but only null says
        // so to a partial update -- see UpdateMeRequest's docstring.
        body: { full_name: input.fullName.trim() || null },
        throwOnError: true,
      });
      return data;
    },
  });

  const onAvatarChanged = async (fileId: string | null): Promise<void> => {
    try {
      const envelope = await setAvatar.mutateAsync(fileId);
      const updated = envelope.data;
      if (updated) {
        applyUpdatedUser(updated);
      }
      // has_uploaded_avatar has just flipped, so the query is enabled now.
      // Invalidate rather than refetch a local handle: the shell's user menu
      // reads the same key through useAvatarSrc, and invalidating updates
      // the picture in the corner at the same moment as the one on this
      // page.
      await queryClient.invalidateQueries({ queryKey: AVATAR_QUERY_KEY });
      toast.success(
        t(
          fileId === null
            ? "auth.profile.avatar_removed"
            : "auth.profile.avatar_saved",
        ),
      );
    } catch (error) {
      toast.error(getErrorMessage(error, "auth.profile.error_avatar"));
    }
  };

  const onSubmit = async (values: ProfileInput): Promise<void> => {
    try {
      const envelope = await updateProfile.mutateAsync(values);
      // The name shows in the app shell too, so the shared auth store has to
      // learn about it -- and from the server's response, which has already
      // normalised a whitespace-only name to null.
      const updated = envelope.data;
      if (updated) {
        applyUpdatedUser(updated);
      }
      toast.success(t("auth.profile.saved"));
    } catch (error) {
      toast.error(getErrorMessage(error, "auth.profile.error_save"));
    }
  };

  return (
    <Page>
      <PageHeader
        title={t("common.nav.profile")}
        description={t("auth.profile.description")}
      />

      {isLoading || !user ? (
        <div className="space-y-4">
          <Skeleton className="h-16 w-16 rounded-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : (
        <>
          {/* Each section saves alone (ADR-0008). The form lives inside its
              section and the footer's Save points at it with `form=`, so
              sections stay direct siblings and space themselves. */}
          <SettingsSection
            title={t("auth.profile.section_details")}
            footer={
              <Button
                type="submit"
                form={detailsFormId}
                disabled={
                  form.formState.isSubmitting || !form.formState.isDirty
                }
              >
                {t("auth.profile.save")}
              </Button>
            }
          >
            <SettingsRow
              label={t("auth.profile.photo_label")}
              hint={t("auth.profile.photo_hint")}
            >
              <Avatar className="size-12" data-testid="profile-avatar">
                {avatarSrc ? <AvatarImage src={avatarSrc} alt="" /> : null}
                <AvatarFallback>{initialsFor(user.email)}</AvatarFallback>
              </Avatar>
              <FileUploader
                variant="button"
                buttonLabel={t("auth.profile.upload_avatar")}
                bucket="avatars"
                accept="image/*"
                maxSize={5 * 1024 * 1024}
                onUploaded={(file) => {
                  // Storing the file was never the point -- until now
                  // nothing recorded *which* file was the avatar, so this
                  // handler showed a toast and the upload vanished.
                  void onAvatarChanged(file.id);
                }}
              />
              {user.has_uploaded_avatar ? (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={setAvatar.isPending}
                  onClick={() => {
                    void onAvatarChanged(null);
                  }}
                  data-testid="profile-avatar-remove"
                >
                  {t("auth.profile.remove_avatar")}
                </Button>
              ) : null}
            </SettingsRow>

            <Form {...form}>
              <form
                id={detailsFormId}
                onSubmit={(event) => {
                  void form.handleSubmit(onSubmit)(event);
                }}
                noValidate
              >
                <FormField
                  control={form.control}
                  name="fullName"
                  render={({ field }) => (
                    <FormItem>
                      <SettingsRow
                        label={
                          <FormLabel>{t("auth.profile.name_label")}</FormLabel>
                        }
                        hint={t("auth.profile.name_hint")}
                      >
                        <div className="w-full max-w-sm space-y-2">
                          <FormControl>
                            {/* No placeholder. `auth.profile.no_name` is an
                                em-dash -- it was the *display* fallback for a
                                profile with no name, and as a placeholder it
                                renders an empty field that looks like it
                                already contains a stray character. */}
                            <Input autoComplete="name" {...field} />
                          </FormControl>
                          <FormMessage />
                        </div>
                      </SettingsRow>
                    </FormItem>
                  )}
                />
              </form>
            </Form>

            {/* Read-only on purpose. Changing an email is an account-takeover
                vector, so it goes through the verify-both-addresses flow on
                the security page (POST /auth/change-email), which is also
                gated on a recent sign-in. */}
            <SettingsRow
              label={t("auth.login.email_label")}
              hint={t("auth.profile.email_hint")}
            >
              <span>{user.email}</span>
              <Link
                to="/$locale/settings/security"
                params={{ locale }}
                className="text-primary underline decoration-primary/35 underline-offset-[3px] hover:decoration-current"
              >
                {t("auth.profile.change_email")}
              </Link>
            </SettingsRow>
          </SettingsSection>

          <SettingsSection title={t("auth.profile.section_preferences")}>
            <SettingsRow
              label={t("common.locale_switcher.label")}
              hint={t("auth.profile.language_hint")}
            >
              <RoutedLocaleSwitcher />
            </SettingsRow>
            {/* ThemeToggle is a radiogroup with its own aria-label, so the
                row's label is a caption, not a <label> for one control. It
                applies immediately: no Save. */}
            <SettingsRow
              label={t("common.theme.label")}
              hint={t("common.theme.hint")}
            >
              <ThemeToggle />
            </SettingsRow>
          </SettingsSection>
        </>
      )}
    </Page>
  );
}
