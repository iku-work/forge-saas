import { Link, Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

// Sent to the *new* address a change-email request names, to prove the
// requester actually controls it before AuthService.confirm_email_change()
// applies the change. See SecurityNotification's email_change_requested
// event (T-226) for the counterpart mailed to the *old* address, which
// carries its own separate cancel link.
export interface ChangeEmailProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  confirm_url: string;
}

export function ChangeEmail({ branding, locale, user_name, confirm_url }: ChangeEmailProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.change_email.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.change_email.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.change_email.body", {}, locale)}</Text>
      <CtaButton href={confirm_url} branding={branding}>
        {t("emails.change_email.button", {}, locale)}
      </CtaButton>
      <Text>
        {t("emails.change_email.paste_link", {}, locale)}
        <br />
        <Link href={confirm_url}>{confirm_url}</Link>
      </Text>
      <Text>{t("emails.change_email.expires", {}, locale)}</Text>
    </BaseLayout>
  );
}

export const changeEmailExampleProps: ChangeEmailProps = {
  branding: exampleBranding,
  user_name: "Alex",
  confirm_url: "https://app.example.com/confirm-email-change?token=example-token",
};

export default function ChangeEmailPreview() {
  return <ChangeEmail {...changeEmailExampleProps} />;
}
