import { Text } from "react-email";

import { t } from "@forge/i18n";

import { BaseLayout, CtaButton, exampleBranding, type Branding } from "../components/BaseLayout.js";

// Mirrors the `event` literals AuthService._enqueue_security_notification_email
// (backend/app/modules/auth/service.py, T-216/T-217/T-218) passes through as
// JSON -- each maps to an `emails.security_notification.events.<event>` copy key.
export type SecurityNotificationEvent =
  | "password_change"
  | "totp_enabled"
  | "totp_disabled"
  | "oauth_linked"
  | "oauth_unlinked"
  | "webauthn_registered"
  | "webauthn_removed"
  | "totp_replay_rejected"
  | "email_change_requested"
  | "email_changed"
  | "totp_disabled_by_admin";

export interface SecurityNotificationProps {
  branding: Branding;
  locale?: string;
  user_name?: string | null;
  event: SecurityNotificationEvent;
  // Only set (and only interpolated into the oauth_linked/oauth_unlinked
  // copy) for the two OAuth events -- absent/ignored for every other event.
  provider?: string | null;
  // Only set (and only interpolated into the email_change_requested/
  // email_changed copy) for T-218's email-change events.
  new_email?: string | null;
  settings_url: string;
}

export function SecurityNotification({
  branding,
  locale,
  user_name,
  event,
  provider,
  new_email,
  settings_url,
}: SecurityNotificationProps) {
  return (
    <BaseLayout
      branding={branding}
      previewText={t("emails.security_notification.title", {}, locale)}
      footerText={t("emails.security_notification.footer", {}, locale)}
    >
      <Text>
        {t("emails.security_notification.greeting", {}, locale)}
        {user_name ? ` ${user_name}` : ""},
      </Text>
      <Text>
        {t(
          `emails.security_notification.events.${event}`,
          { provider: provider ?? "", new_email: new_email ?? "" },
          locale,
        )}
      </Text>
      <CtaButton href={settings_url} branding={branding}>
        {t("emails.security_notification.button", {}, locale)}
      </CtaButton>
    </BaseLayout>
  );
}

export const securityNotificationExampleProps: SecurityNotificationProps = {
  branding: exampleBranding,
  user_name: "Alex",
  event: "password_change",
  settings_url: "https://app.example.com/settings/security",
};

export default function SecurityNotificationPreview() {
  return <SecurityNotification {...securityNotificationExampleProps} />;
}
