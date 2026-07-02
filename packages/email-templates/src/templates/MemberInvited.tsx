import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface MemberInvitedProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  member_name: string;
  org_name: string;
  role: string;
  org_url: string;
}

export function MemberInvited({
  branding,
  locale,
  user_name,
  member_name,
  org_name,
  role,
  org_url,
}: MemberInvitedProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.member_invited.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.member_invited.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.member_invited.body", { member_name, org_name, role }, locale)}</Text>
      <CtaButton href={org_url} branding={branding}>
        {t("emails.member_invited.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const memberInvitedExampleProps: MemberInvitedProps = {
  branding: exampleBranding,
  user_name: "Alex",
  member_name: "Jordan Lee",
  org_name: "Acme Inc",
  role: "member",
  org_url: "https://app.example.com/settings/members",
};

export default function MemberInvitedPreview() {
  return <MemberInvited {...memberInvitedExampleProps} />;
}
