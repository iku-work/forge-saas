import { Link, Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface MagicLinkProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  login_url: string;
}

export function MagicLink({ branding, locale, user_name, login_url }: MagicLinkProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.magic_link.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.magic_link.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.magic_link.body", {}, locale)}</Text>
      <CtaButton href={login_url} branding={branding}>
        {t("emails.magic_link.button", {}, locale)}
      </CtaButton>
      <Text>
        {t("emails.magic_link.paste_link", {}, locale)}
        <br />
        <Link href={login_url}>{login_url}</Link>
      </Text>
      <Text>{t("emails.magic_link.expires", {}, locale)}</Text>
    </BaseLayout>
  );
}

export const magicLinkExampleProps: MagicLinkProps = {
  branding: exampleBranding,
  user_name: "Alex",
  login_url: "https://app.example.com/magic-link/consume?token=example-token",
};

export default function MagicLinkPreview() {
  return <MagicLink {...magicLinkExampleProps} />;
}
