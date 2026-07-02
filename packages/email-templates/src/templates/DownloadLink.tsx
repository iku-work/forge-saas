import { Link, Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface DownloadLinkProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  download_url: string;
}

export function DownloadLink({ branding, locale, user_name, download_url }: DownloadLinkProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.download_link.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.download_link.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.download_link.body", {}, locale)}</Text>
      <CtaButton href={download_url} branding={branding}>
        {t("emails.download_link.button", {}, locale)}
      </CtaButton>
      <Text>
        {t("emails.download_link.paste_link", {}, locale)}
        <br />
        <Link href={download_url}>{download_url}</Link>
      </Text>
      <Text>{t("emails.download_link.expires", {}, locale)}</Text>
    </BaseLayout>
  );
}

export const downloadLinkExampleProps: DownloadLinkProps = {
  branding: exampleBranding,
  user_name: "Alex",
  download_url: "https://storage.example.com/exports/example.zip?token=example-token",
};

export default function DownloadLinkPreview() {
  return <DownloadLink {...downloadLinkExampleProps} />;
}
