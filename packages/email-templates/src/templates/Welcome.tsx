import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface WelcomeProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  app_url: string;
}

export function Welcome({ branding, locale, user_name, app_url }: WelcomeProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.welcome.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.welcome.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.welcome.body", {}, locale)}</Text>
      <CtaButton href={app_url} branding={branding}>
        {t("emails.welcome.button", {}, locale)}
      </CtaButton>
      <Text>{t("emails.welcome.closing", {}, locale)}</Text>
    </BaseLayout>
  );
}

export const welcomeExampleProps: WelcomeProps = {
  branding: exampleBranding,
  user_name: "Alex",
  app_url: "https://app.example.com/dashboard",
};

export default function WelcomePreview() {
  return <Welcome {...welcomeExampleProps} />;
}
