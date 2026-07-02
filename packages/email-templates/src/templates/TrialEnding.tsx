import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface TrialEndingProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  org_name: string;
  trial_end_date: string;
  upgrade_url: string;
}

export function TrialEnding({
  branding,
  locale,
  user_name,
  org_name,
  trial_end_date,
  upgrade_url,
}: TrialEndingProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.trial_ending.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.trial_ending.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.trial_ending.body", { org_name, trial_end_date }, locale)}</Text>
      <CtaButton href={upgrade_url} branding={branding}>
        {t("emails.trial_ending.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const trialEndingExampleProps: TrialEndingProps = {
  branding: exampleBranding,
  user_name: "Alex",
  org_name: "Acme Inc",
  trial_end_date: "July 15, 2026",
  upgrade_url: "https://app.example.com/settings/billing",
};

export default function TrialEndingPreview() {
  return <TrialEnding {...trialEndingExampleProps} />;
}
