import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

/**
 * T-234: the email channel's fallback template for notification types that
 * have no rich, purpose-built template of their own (file_uploaded,
 * file_infected, member_invited, weekly_summary...). Unlike every other
 * template here, its `title`/`body` are *already translated* by the time they
 * arrive: NotificationService.send() renders
 * `notifications.types.<type>.title/body` in the recipient's own locale
 * before enqueuing send_email, so the same event reaches an `en` and a `fi`
 * recipient in their respective languages. Only this template's own chrome
 * (greeting, CTA label, footer) is looked up here, from the `locale` prop the
 * worker passes alongside them.
 *
 * Before this existed the notification email channel enqueued
 * `template=<notification type>` with a `{title, body, action_url}` context
 * no such template accepted -- `file_infected` had no template at all (the
 * renderer failed the job outright) and `member_invited` silently rendered a
 * different email ("X has joined") with every interpolation undefined.
 */
export interface NotificationEmailProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  title: string;
  body: string;
  action_url?: string | null;
}

export function NotificationEmail({
  branding,
  locale,
  user_name,
  title,
  body,
  action_url,
}: NotificationEmailProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={title}
      footerText={t("emails.footer.ignore_notice", {}, locale)}
    >
      <Text>
        {t("emails.notification.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text style={{ fontWeight: "bold" }}>{title}</Text>
      <Text>{body}</Text>
      {action_url ? (
        <CtaButton href={action_url} branding={branding}>
          {t("emails.notification.button", {}, locale)}
        </CtaButton>
      ) : null}
    </BaseLayout>
  );
}

export const notificationEmailExampleProps: NotificationEmailProps = {
  branding: exampleBranding,
  user_name: "Alex",
  title: "File uploaded",
  body: "q3-report.pdf finished uploading.",
  action_url: "https://app.example.com/files/example-id",
};

export default function NotificationEmailPreview() {
  return <NotificationEmail {...notificationEmailExampleProps} />;
}
