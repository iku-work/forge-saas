import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface SubscriptionExpiringProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  org_name: string;
  plan_name: string;
  expires_at: string;
  billing_url: string;
}

export function SubscriptionExpiring({
  branding,
  locale,
  user_name,
  org_name,
  plan_name,
  expires_at,
  billing_url,
}: SubscriptionExpiringProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.subscription_expiring.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.subscription_expiring.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>
        {t("emails.subscription_expiring.body", { org_name, plan_name, expires_at }, locale)}
      </Text>
      <CtaButton href={billing_url} branding={branding}>
        {t("emails.subscription_expiring.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const subscriptionExpiringExampleProps: SubscriptionExpiringProps = {
  branding: exampleBranding,
  user_name: "Alex",
  org_name: "Acme Inc",
  plan_name: "Pro",
  expires_at: "July 15, 2026",
  billing_url: "https://app.example.com/settings/billing",
};

export default function SubscriptionExpiringPreview() {
  return <SubscriptionExpiring {...subscriptionExpiringExampleProps} />;
}
