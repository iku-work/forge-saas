import { Link, Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface InvitationProps {
  branding: Branding;
  locale?: string;
  inviter_name?: string | null;
  org_name: string;
  accept_url: string;
}

export function Invitation({ branding, locale, inviter_name, org_name, accept_url }: InvitationProps) {
  const body = inviter_name
    ? t("emails.invitation.body_with_inviter", { inviter_name, org_name }, locale)
    : t("emails.invitation.body_without_inviter", { org_name }, locale);

  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.invitation.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>{t("emails.invitation.greeting", {}, locale)}</Text>
      <Text>{body}</Text>
      <CtaButton href={accept_url} branding={branding}>
        {t("emails.invitation.button", {}, locale)}
      </CtaButton>
      <Text>
        {t("emails.invitation.paste_link", {}, locale)}
        <br />
        <Link href={accept_url}>{accept_url}</Link>
      </Text>
      <Text>{t("emails.invitation.expires", {}, locale)}</Text>
    </BaseLayout>
  );
}

export const invitationExampleProps: InvitationProps = {
  branding: exampleBranding,
  inviter_name: "Jordan",
  org_name: "Acme Inc",
  accept_url: "https://app.example.com/invitations/example-token",
};

export default function InvitationPreview() {
  return <Invitation {...invitationExampleProps} />;
}
