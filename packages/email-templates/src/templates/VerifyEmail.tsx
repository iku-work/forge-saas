import { Link, Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface VerifyEmailProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  verification_url: string;
}

export function VerifyEmail({ branding, locale, user_name, verification_url }: VerifyEmailProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.verify_email.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.verify_email.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.verify_email.body", {}, locale)}</Text>
      <CtaButton href={verification_url} branding={branding}>
        {t("emails.verify_email.button", {}, locale)}
      </CtaButton>
      <Text>
        {t("emails.verify_email.paste_link", {}, locale)}
        <br />
        <Link href={verification_url}>{verification_url}</Link>
      </Text>
      <Text>{t("emails.verify_email.expires", {}, locale)}</Text>
    </BaseLayout>
  );
}

export const verifyEmailExampleProps: VerifyEmailProps = {
  branding: exampleBranding,
  user_name: "Alex",
  verification_url: "https://app.example.com/verify-email?token=example-token",
};

export default function VerifyEmailPreview() {
  return <VerifyEmail {...verifyEmailExampleProps} />;
}
