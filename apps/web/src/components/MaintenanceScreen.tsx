import { Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { MaintenanceState } from "@forge/hooks";

/**
 * T-263: what a signed-in user sees while maintenance mode is on.
 *
 * Without this, a maintenance window looks like a broken app: every request
 * answers 503 and the user gets a wall of error toasts on a half-rendered
 * shell. `GET /api/v1/config` is deliberately exempt from the middleware
 * precisely so the client can tell "we took it down on purpose, here is why"
 * apart from "the network is gone".
 *
 * The message is the operator's own prose, rendered verbatim -- see
 * GlobalBanner.tsx for why that is not translated. Only the fallback, for an
 * operator who flipped the switch without writing any copy, comes from the
 * catalog.
 *
 * No retry button on purpose: `useMaintenance()` polls `/config`, so this
 * screen removes itself within a minute of the window ending. A button that
 * reloads into the same screen teaches users to hammer it.
 */
export function MaintenanceScreen({
  maintenance,
}: {
  maintenance: MaintenanceState;
}) {
  const { t } = useTranslation();

  return (
    <div
      role="alert"
      data-testid="maintenance-screen"
      className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center"
    >
      <Wrench className="size-10 text-muted-foreground" aria-hidden="true" />
      <h1 className="text-xl font-semibold">{t("common.maintenance.title")}</h1>
      <p className="max-w-prose text-sm text-muted-foreground">
        {maintenance.message || t("common.maintenance.default_message")}
      </p>
    </div>
  );
}
