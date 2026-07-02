import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface PaymentFailedProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  org_name: string;
  amount: string;
  billing_url: string;
}

export function PaymentFailed({
  branding,
  locale,
  user_name,
  org_name,
  amount,
  billing_url,
}: PaymentFailedProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.payment_failed.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.payment_failed.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.payment_failed.body", { org_name, amount }, locale)}</Text>
      <CtaButton href={billing_url} branding={branding}>
        {t("emails.payment_failed.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const paymentFailedExampleProps: PaymentFailedProps = {
  branding: exampleBranding,
  user_name: "Alex",
  org_name: "Acme Inc",
  amount: "$49.00",
  billing_url: "https://app.example.com/settings/billing",
};

export default function PaymentFailedPreview() {
  return <PaymentFailed {...paymentFailedExampleProps} />;
}
