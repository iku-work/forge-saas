import { Link, Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface PasswordResetProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  reset_url: string;
}

export function PasswordReset({ branding, locale, user_name, reset_url }: PasswordResetProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.password_reset.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.password_reset.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.password_reset.body", {}, locale)}</Text>
      <CtaButton href={reset_url} branding={branding}>
        {t("emails.password_reset.button", {}, locale)}
      </CtaButton>
      <Text>
        {t("emails.password_reset.paste_link", {}, locale)}
        <br />
        <Link href={reset_url}>{reset_url}</Link>
      </Text>
      <Text>{t("emails.password_reset.expires", {}, locale)}</Text>
    </BaseLayout>
  );
}

export const passwordResetExampleProps: PasswordResetProps = {
  branding: exampleBranding,
  user_name: "Alex",
  reset_url: "https://app.example.com/reset-password?token=example-token",
};

export default function PasswordResetPreview() {
  return <PasswordReset {...passwordResetExampleProps} />;
}
