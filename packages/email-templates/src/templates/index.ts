import type { ReactElement } from "react";

import { ChangeEmail, changeEmailExampleProps } from "./ChangeEmail.js";
import { DownloadLink, downloadLinkExampleProps } from "./DownloadLink.js";
import { FileUploaded, fileUploadedExampleProps } from "./FileUploaded.js";
import { Invitation, invitationExampleProps } from "./Invitation.js";
import { MagicLink, magicLinkExampleProps } from "./MagicLink.js";
import { MemberInvited, memberInvitedExampleProps } from "./MemberInvited.js";
import { PasswordReset, passwordResetExampleProps } from "./PasswordReset.js";
import { PaymentFailed, paymentFailedExampleProps } from "./PaymentFailed.js";
import {
  SecurityNotification,
  securityNotificationExampleProps,
} from "./SecurityNotification.js";
import {
  SubscriptionExpiring,
  subscriptionExpiringExampleProps,
} from "./SubscriptionExpiring.js";
import {
  SubscriptionSuspended,
  subscriptionSuspendedExampleProps,
} from "./SubscriptionSuspended.js";
import { TrialEnding, trialEndingExampleProps } from "./TrialEnding.js";
import { TrialExpired, trialExpiredExampleProps } from "./TrialExpired.js";
import { VerifyEmail, verifyEmailExampleProps } from "./VerifyEmail.js";
import { Welcome, welcomeExampleProps } from "./Welcome.js";

interface TemplateEntry {
  render: (props: object) => ReactElement;
  exampleProps: object;
}

/**
 * The registry is keyed by template name (a plain string the Python worker
 * sends as JSON), so a lookup can only narrow to `object` -- `props as P` is
 * the one place that bridges back to a component's real, named props. It's
 * sound rather than `any` because every production caller supplies the same
 * fields `exampleProps` does (the Python call sites are written against
 * these exact field names -- see backend/app/workers/jobs/email.py).
 *
 * Each template's Props interface is a closed named type (not a fresh object
 * literal), so it has no index signature -- that rules out constraining P to
 * Record<string, unknown> here (TS won't assign a named interface to that
 * without one). `object` is the loosest constraint that still lets `props as
 * P` narrow safely below.
 *
 * `exampleProps` comes first so P is inferred from that (ordinary covariant)
 * argument alone; Component is then merely checked against the already-
 * solved P instead of contributing its own (contravariant, and therefore
 * widened) inference candidate.
 */
function defineTemplate<P extends object>(
  exampleProps: P,
  Component: (props: P) => ReactElement,
): TemplateEntry {
  return { render: (props) => Component(props as P), exampleProps };
}

export const TEMPLATES = {
  verify_email: defineTemplate(verifyEmailExampleProps, VerifyEmail),
  change_email: defineTemplate(changeEmailExampleProps, ChangeEmail),
  password_reset: defineTemplate(passwordResetExampleProps, PasswordReset),
  magic_link: defineTemplate(magicLinkExampleProps, MagicLink),
  invitation: defineTemplate(invitationExampleProps, Invitation),
  welcome: defineTemplate(welcomeExampleProps, Welcome),
  download_link: defineTemplate(downloadLinkExampleProps, DownloadLink),
  member_invited: defineTemplate(memberInvitedExampleProps, MemberInvited),
  payment_failed: defineTemplate(paymentFailedExampleProps, PaymentFailed),
  subscription_expiring: defineTemplate(subscriptionExpiringExampleProps, SubscriptionExpiring),
  subscription_suspended: defineTemplate(
    subscriptionSuspendedExampleProps,
    SubscriptionSuspended,
  ),
  trial_ending: defineTemplate(trialEndingExampleProps, TrialEnding),
  trial_expired: defineTemplate(trialExpiredExampleProps, TrialExpired),
  file_uploaded: defineTemplate(fileUploadedExampleProps, FileUploaded),
  security_notification: defineTemplate(securityNotificationExampleProps, SecurityNotification),
} satisfies Record<string, TemplateEntry>;

export type TemplateName = keyof typeof TEMPLATES;

export const TEMPLATE_NAMES = Object.keys(TEMPLATES) as TemplateName[];

export function isTemplateName(name: string): name is TemplateName {
  return Object.hasOwn(TEMPLATES, name);
}
