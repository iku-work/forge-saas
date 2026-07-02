import { useState } from "react";
import { useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import { Text, View } from "react-native";

import { Banner } from "../../src/components/Banner";
import { Button } from "../../src/components/Button";
import { LanguagePicker } from "../../src/components/LanguagePicker";
import { logout } from "../../src/lib/auth";
import {
  pickAndUpload,
  UploadPermissionError,
} from "../../src/lib/uploads";

export default function SettingsScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false);
  const [avatarMessage, setAvatarMessage] = useState<{
    text: string;
    variant: "error" | "info";
  } | null>(null);

  const handleLogout = async (): Promise<void> => {
    setIsLoggingOut(true);
    try {
      await logout();
    } finally {
      router.replace("/login");
    }
  };

  const handleAvatarUpload = (): void => {
    setAvatarMessage(null);
    setIsUploadingAvatar(true);
    const handle = pickAndUpload({ source: "library", bucket: "avatars" });
    void handle.result
      .then((uploaded) => {
        if (uploaded) {
          setAvatarMessage({
            text: t("files.picker.avatar_uploaded"),
            variant: "info",
          });
        }
      })
      .catch((error: unknown) => {
        setAvatarMessage({
          text:
            error instanceof UploadPermissionError
              ? t("files.picker.permission_denied")
              : t("files.picker.error_upload"),
          variant: "error",
        });
      })
      .finally(() => {
        setIsUploadingAvatar(false);
      });
  };

  return (
    <View className="flex-1 bg-white p-6">
      <Text className="text-2xl font-semibold text-slate-900">
        {t("common.nav.settings")}
      </Text>
      <Text className="mt-2 text-sm text-slate-400">
        {t("common.dashboard.empty")}
      </Text>
      <View className="mt-6">
        <Button
          label={t("files.picker.avatar_button")}
          hint={t("files.picker.avatar_hint")}
          variant="outline"
          disabled={isUploadingAvatar}
          onPress={handleAvatarUpload}
        />
        {avatarMessage ? (
          <View className="mt-2">
            <Banner variant={avatarMessage.variant}>
              {avatarMessage.text}
            </Banner>
          </View>
        ) : null}
      </View>
      <View className="mt-6">
        <LanguagePicker />
      </View>
      <View className="mt-6">
        <Button
          label={
            isLoggingOut
              ? t("common.status.logging_out")
              : t("common.nav.logout")
          }
          hint="Signs out of this account on this device"
          variant="outline"
          disabled={isLoggingOut}
          onPress={() => {
            void handleLogout();
          }}
        />
      </View>
    </View>
  );
}
