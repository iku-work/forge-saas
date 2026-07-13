import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@forge/hooks";
import {
  Avatar,
  AvatarFallback,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  FileUploader,
  Label,
  LocaleSwitcher,
  Skeleton,
} from "@forge/ui";

function initialsFor(email: string): string {
  return email.slice(0, 2).toUpperCase();
}

export function ProfileSettingsPage() {
  const { t } = useTranslation();
  const { user, isLoading } = useAuth();

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
                  onUploaded={() => {
                    toast.success(t("auth.profile.upload_avatar"));
                  }}
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="profile-name">
                {t("auth.profile.name_label")}
              </Label>
              <p id="profile-name" className="text-sm">
                {user.full_name ?? t("auth.profile.no_name")}
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="profile-email">
                {t("auth.login.email_label")}
              </Label>
              <p id="profile-email" className="text-sm">
                {user.email}
              </p>
            </div>
            <div className="space-y-1">
              <Label>{t("common.locale_switcher.label")}</Label>
              <div>
                <LocaleSwitcher />
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
