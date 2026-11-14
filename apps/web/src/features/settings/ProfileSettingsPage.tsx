import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useId, useRef } from "react";
import { Link } from "@tanstack/react-router";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { z } from "zod";

import {
  deleteMyAvatarApiV1AuthMeAvatarDelete,
  getAvatarUploadFormApiV1AuthMeAvatarUploadFormPost,
  meApiV1AuthMeGet,
  setMyAvatarApiV1AuthMeAvatarPost,
  updateMeApiV1AuthMePatch,
} from "@forge/api-client";
import type { UserPublic } from "@forge/api-client";
import { useAuth } from "@forge/hooks";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
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
import { ThemeToggle } from "../../components/ThemeToggle";
import { RoutedLocaleSwitcher } from "../../lib/RoutedLocaleSwitcher";
import { useAvatarSrc } from "../../lib/useAvatarSrc";
import { useRouteLocale } from "../../lib/useRouteLocale";

// What the server accepts: AvatarUploadFormRequest and MAX_AVATAR_BYTES.
const AVATAR_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
type AvatarType = (typeof AVATAR_TYPES)[number];
const AVATAR_MAX_BYTES = 5_000_000;
// The scanner worker usually finishes in a second or two.
const AVATAR_POLL_MS = 1_500;
const AVATAR_POLL_LIMIT = 40;

function isAvatarType(type: string): type is AvatarType {
  return (AVATAR_TYPES as readonly string[]).includes(type);
}

class AvatarRejectedError extends Error {}

/**
 * Upload `file` as the avatar and wait until the scanner worker has dealt
 * with it. Resolves with the user once it is published; throws
 * AvatarRejectedError if it was not.
 *
 * The file goes straight to storage through a presigned POST form, like
 * every upload (see FileUploader for the form's two ordering rules), and the
 * server then scans and re-encodes it in the background.
 */
async function uploadAvatar(file: File): Promise<UserPublic> {
  if (!isAvatarType(file.type) || file.size > AVATAR_MAX_BYTES) {
    throw new AvatarRejectedError();
  }
  const { data: formEnvelope } =
    await getAvatarUploadFormApiV1AuthMeAvatarUploadFormPost({
      body: { content_type: file.type },
      throwOnError: true,
    });
  const form = formEnvelope.data;
  if (!form) throw new Error("no upload form");
  const body = new FormData();
  for (const [name, value] of Object.entries(form.fields)) {
    body.append(name, value);
  }
  body.append("file", file);
  const stored = await fetch(form.upload_url, { method: "POST", body });
  if (!stored.ok) throw new Error(`upload failed: ${String(stored.status)}`);

  const { data: setEnvelope } = await setMyAvatarApiV1AuthMeAvatarPost({
    body: { upload_key: form.upload_key },
    throwOnError: true,
  });
  let user = setEnvelope.data;
  for (
    let poll = 0;
    user?.avatar_processing && poll < AVATAR_POLL_LIMIT;
    poll++
  ) {
    await new Promise((resolve) => setTimeout(resolve, AVATAR_POLL_MS));
    user = (await meApiV1AuthMeGet({ throwOnError: true })).data.data;
  }
  if (!user || user.avatar_processing) throw new Error("still processing");
  // Processing cleared without a new picture: the worker refused the file.
  if (user.avatar_url === setEnvelope.data?.avatar_url) {
    throw new AvatarRejectedError();
  }
  return user;
}

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
  const avatarInput = useRef<HTMLInputElement>(null);

  const setAvatar = useMutation({
    // null removes the photo.
    mutationFn: async (file: File | null) => {
      if (file) return uploadAvatar(file);
      const { data } = await deleteMyAvatarApiV1AuthMeAvatarDelete({
        throwOnError: true,
      });
      return data.data;
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

  const onAvatarChanged = async (file: File | null): Promise<void> => {
    try {
      const updated = await setAvatar.mutateAsync(file);
      // The shell's user menu reads the same store, so it changes too.
      if (updated) applyUpdatedUser(updated);
      toast.success(
        t(
          file === null
            ? "auth.profile.avatar_removed"
            : "auth.profile.avatar_saved",
        ),
      );
    } catch (error) {
      toast.error(
        error instanceof AvatarRejectedError
          ? t("auth.profile.avatar_rejected")
          : getErrorMessage(error, "auth.profile.error_avatar"),
      );
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
              <input
                ref={avatarInput}
                type="file"
                accept={AVATAR_TYPES.join(",")}
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                data-testid="profile-avatar-input"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void onAvatarChanged(file);
                }}
              />
              <Button
                type="button"
                variant="outline"
                disabled={setAvatar.isPending}
                onClick={() => avatarInput.current?.click()}
                data-testid="profile-avatar-upload"
              >
                {setAvatar.isPending
                  ? t("auth.profile.avatar_processing")
                  : t("auth.profile.upload_avatar")}
              </Button>
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
