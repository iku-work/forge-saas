import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface SubscriptionSuspendedProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  org_name: string;
  plan_name: string;
  billing_url: string;
}

export function SubscriptionSuspended({
  branding,
  locale,
  user_name,
  org_name,
  plan_name,
  billing_url,
}: SubscriptionSuspendedProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.subscription_suspended.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.subscription_suspended.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.subscription_suspended.body", { org_name, plan_name }, locale)}</Text>
      <CtaButton href={billing_url} branding={branding}>
        {t("emails.subscription_suspended.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const subscriptionSuspendedExampleProps: SubscriptionSuspendedProps = {
  branding: exampleBranding,
  user_name: "Alex",
  org_name: "Acme Inc",
  plan_name: "Pro",
  billing_url: "https://app.example.com/settings/billing",
};

export default function SubscriptionSuspendedPreview() {
  return <SubscriptionSuspended {...subscriptionSuspendedExampleProps} />;
}
