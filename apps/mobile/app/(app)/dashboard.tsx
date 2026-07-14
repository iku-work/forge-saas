import { router } from "expo-router";
import { useTranslation } from "react-i18next";
import { Text, View } from "react-native";

import { Button } from "../../src/components/Button";

export default function DashboardScreen() {
  const { t } = useTranslation();

  return (
    <View className="flex-1 bg-white p-6">
      <Text className="text-2xl font-semibold text-slate-900">
        {t("common.nav.dashboard")}
      </Text>
      <Text className="mt-2 text-sm text-slate-400">
        {t("common.dashboard.empty")}
      </Text>
      <View className="mt-6 gap-3">
        <Button
          label={t("common.nav.files")}
          hint={t("common.nav.files_hint")}
          variant="outline"
          onPress={() => {
            router.push("/files");
          }}
        />
        <Button
          label={t("common.nav.notifications")}
          hint={t("common.nav.notifications_hint")}
          variant="outline"
          onPress={() => {
            router.push("/notifications");
          }}
        />
        <Button
          label={t("common.nav.settings")}
          hint={t("common.nav.settings_hint")}
          variant="outline"
          onPress={() => {
            router.push("/settings");
          }}
        />
      </View>
    </View>
  );
}
