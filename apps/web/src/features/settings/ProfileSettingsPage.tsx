import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { z } from "zod";

import {
  getMyAvatarApiV1AuthMeAvatarGet,
  updateMeApiV1AuthMePatch,
} from "@forge/api-client";
import { useAuth } from "@forge/hooks";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  FileUploader,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Label,
  Skeleton,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";
import { RoutedLocaleSwitcher } from "../../lib/RoutedLocaleSwitcher";

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

  // The avatars bucket is private, so there is no URL on the user object to
  // render -- what comes back here is a presigned link that expires in five
  // minutes. Fetched only when the user actually has an uploaded avatar; an
  // OAuth `picture` is already an absolute URL on user.avatar_url.
  const avatarQuery = useQuery({
    queryKey: ["me", "avatar"],
    enabled: user?.has_uploaded_avatar === true,
    // Refetched well inside the signature's lifetime: a stale src would
    // simply stop loading, with nothing on the page to explain why.
    staleTime: 4 * 60 * 1000,
    queryFn: async () => {
      const { data } = await getMyAvatarApiV1AuthMeAvatarGet({
        throwOnError: true,
      });
      return data;
    },
  });

  const setAvatar = useMutation({
    mutationFn: async (fileId: string) => {
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

  // An uploaded avatar wins over an OAuth picture: the upload is a
  // deliberate choice, the picture is a default someone else supplied.
  const avatarSrc = avatarQuery.data?.data?.url ?? user?.avatar_url ?? null;

  const onAvatarUploaded = async (fileId: string): Promise<void> => {
    try {
      const envelope = await setAvatar.mutateAsync(fileId);
      const updated = envelope.data;
      if (updated) {
        applyUpdatedUser(updated);
      }
      // has_uploaded_avatar has just flipped, so the query above is enabled
      // now; refetch rather than wait for a remount.
      await avatarQuery.refetch();
      toast.success(t("auth.profile.avatar_saved"));
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
    <div className="max-w-xl space-y-6 p-6">
      <h1 className="text-xl font-semibold">{t("common.nav.profile")}</h1>

      {isLoading || !user ? (
        <div className="space-y-4">
          <Skeleton className="h-16 w-16 rounded-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : (
        <Card>
          <CardHeader>
            <CardDescription>{t("auth.profile.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="flex items-start gap-4">
              <Avatar className="size-16" data-testid="profile-avatar">
                {avatarSrc ? <AvatarImage src={avatarSrc} alt="" /> : null}
                <AvatarFallback className="text-lg">
                  {initialsFor(user.email)}
                </AvatarFallback>
              </Avatar>
              <div className="flex-1">
                <p className="mb-2 text-sm font-medium">
                  {t("auth.profile.upload_avatar")}
                </p>
                <FileUploader
                  bucket="avatars"
                  accept="image/*"
                  maxSize={5 * 1024 * 1024}
                  onUploaded={(file) => {
                    // Storing the file was never the point -- until now
                    // nothing recorded *which* file was the avatar, so this
                    // handler showed a toast and the upload vanished.
                    void onAvatarUploaded(file.id);
                  }}
                />
              </div>
            </div>

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
                  name="fullName"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("auth.profile.name_label")}</FormLabel>
                      <FormControl>
                        {/* No placeholder. `auth.profile.no_name` is an
                            em-dash -- it was the *display* fallback for a
                            profile with no name, and as a placeholder it
                            renders an empty field that looks like it already
                            contains a stray character. The label says Name;
                            an empty field needs no further explanation. */}
                        <Input autoComplete="name" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  disabled={
                    form.formState.isSubmitting || !form.formState.isDirty
                  }
                >
                  {t("auth.profile.save")}
                </Button>
              </form>
            </Form>

            <div className="space-y-1">
              <Label htmlFor="profile-email">
                {t("auth.login.email_label")}
              </Label>
              {/* Read-only on purpose. Changing an email is an account-takeover
                  vector, so it goes through the verify-both-addresses flow on
                  the security page (POST /auth/change-email), which is also
                  gated on a recent sign-in. */}
              <p id="profile-email" className="text-sm">
                {user.email}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("auth.profile.email_change_hint")}
              </p>
            </div>

            <div className="space-y-1">
              <Label>{t("common.locale_switcher.label")}</Label>
              <div>
                <RoutedLocaleSwitcher />
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
