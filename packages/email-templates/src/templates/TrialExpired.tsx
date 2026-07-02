import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface TrialExpiredProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  org_name: string;
  billing_url: string;
}

export function TrialExpired({ branding, locale, user_name, org_name, billing_url }: TrialExpiredProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.trial_expired.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.trial_expired.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.trial_expired.body", { org_name }, locale)}</Text>
      <CtaButton href={billing_url} branding={branding}>
        {t("emails.trial_expired.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const trialExpiredExampleProps: TrialExpiredProps = {
  branding: exampleBranding,
  user_name: "Alex",
  org_name: "Acme Inc",
  billing_url: "https://app.example.com/settings/billing",
};

export default function TrialExpiredPreview() {
  return <TrialExpired {...trialExpiredExampleProps} />;
}
