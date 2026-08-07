import { useTranslation } from "react-i18next";
import { Text, View } from "react-native";

import type { MaintenanceState } from "../lib/config";

/**
 * T-263: mobile's counterpart to apps/web's MaintenanceScreen.
 *
 * Mounted in the root layout *in place of* `<Slot />` while a window is open,
 * so no screen renders behind it. Without it, a maintenance window looks like
 * a broken install: every request answers 503 and the user gets error states
 * on screens that never finish loading -- which on mobile is the kind of thing
 * that earns a one-star review rather than a support ticket.
 *
 * `GET /api/v1/config` is exempt from the middleware precisely so the app can
 * tell "taken down on purpose, here is why" apart from "no signal", and
 * `useMaintenance()` polls it, so this screen removes itself within a minute
 * of the window ending -- no reload, no relaunch, no retry button to hammer.
 *
 * Takes the state as a prop rather than reading the hook itself (mirroring
 * apps/web's): the caller has already read it to decide whether to render
 * this at all, and each `useMaintenance()` call starts its own poll timer.
 */
export function MaintenanceScreen({
  maintenance,
}: {
  maintenance: MaintenanceState;
}) {
  const { t } = useTranslation();

  return (
    <View
      accessibilityRole="alert"
      className="flex-1 items-center justify-center gap-3 bg-white p-6"
    >
      <Text
        accessibilityRole="header"
        className="text-xl font-semibold text-slate-900"
      >
        {t("common.maintenance.title")}
      </Text>
      {/* The operator's own prose, rendered verbatim -- only the fallback,
          for a window opened without any copy, is translated. */}
      <Text className="text-center text-sm text-slate-600">
        {maintenance.message || t("common.maintenance.default_message")}
      </Text>
    </View>
  );
}
