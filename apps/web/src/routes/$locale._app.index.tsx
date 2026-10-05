import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { useOrg } from "@forge/hooks";
import { Page, PageHeader } from "@forge/ui";

import { GettingStarted } from "../features/dashboard/GettingStarted";
import { useGettingStarted } from "../features/dashboard/useGettingStarted";
import { isAppleDevice, renderKey } from "../features/shortcuts/registry";

export const Route = createFileRoute("/$locale/_app/")({
  component: DashboardPage,
});

/**
 * The first authenticated screen a new account sees, which makes it the
 * single highest-leverage empty state in the product (F-010, §1.5's halo
 * effect). It shows a Getting started checklist built from what the
 * organization has actually done, and goes quiet once everything is done.
 */
function DashboardPage() {
  const { t } = useTranslation();
  const { org } = useOrg();
  const gettingStarted = useGettingStarted();
  const allDone =
    !gettingStarted.isLoading &&
    gettingStarted.steps.every((step) => step.done);

  return (
    <Page>
      <PageHeader
        title={
          org
            ? t("common.dashboard.welcome_title", { org: org.name })
            : t("common.nav.dashboard")
        }
        description={
          allDone
            ? t("common.dashboard.ready_body")
            : t("common.dashboard.welcome_body")
        }
      />
      <GettingStarted
        state={gettingStarted}
        footer={
          <p className="mt-7 text-[0.8125rem] text-muted-foreground">
            {t("common.dashboard.shortcut_note", {
              shortcut: `${renderKey("mod", isAppleDevice())}K`,
            })}
          </p>
        }
      />
    </Page>
  );
}
