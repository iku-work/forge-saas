import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

export interface FileUploadedProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  org_name: string;
  filename: string;
  uploaded_by: string;
  file_url: string;
}

export function FileUploaded({
  branding,
  locale,
  user_name,
  org_name,
  filename,
  uploaded_by,
  file_url,
}: FileUploadedProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.file_uploaded.title", {}, locale)}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.file_uploaded.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>{t("emails.file_uploaded.body", { filename, uploaded_by, org_name }, locale)}</Text>
      <CtaButton href={file_url} branding={branding}>
        {t("emails.file_uploaded.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const fileUploadedExampleProps: FileUploadedProps = {
  branding: exampleBranding,
  user_name: "Alex",
  org_name: "Acme Inc",
  filename: "q3-report.pdf",
  uploaded_by: "Jordan Lee",
  file_url: "https://app.example.com/files/example-id",
};

export default function FileUploadedPreview() {
  return <FileUploaded {...fileUploadedExampleProps} />;
}
