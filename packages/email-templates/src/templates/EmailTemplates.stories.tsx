import type { Meta, StoryObj } from "@storybook/react-vite";

import { downloadLinkExampleProps, DownloadLink } from "./DownloadLink.js";
import { EmailPreview } from "./EmailPreview.js";
import { fileUploadedExampleProps, FileUploaded } from "./FileUploaded.js";
import { invitationExampleProps, Invitation } from "./Invitation.js";
import { magicLinkExampleProps, MagicLink } from "./MagicLink.js";
import { memberInvitedExampleProps, MemberInvited } from "./MemberInvited.js";
import { passwordResetExampleProps, PasswordReset } from "./PasswordReset.js";
import { paymentFailedExampleProps, PaymentFailed } from "./PaymentFailed.js";
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

const meta = {
  title: "Emails/Templates",
  component: EmailPreview,
  // Every story below overrides `render` (and so never reads `args`) --
  // this default only exists to satisfy EmailPreview's required `element`
  // prop at the type level.
  args: { element: <Welcome {...welcomeExampleProps} /> },
} satisfies Meta<typeof EmailPreview>;

export default meta;
type Story = StoryObj<typeof meta>;

// Each story renders its template with the exact example props already
// shipped in the template's own file (the same ones the CLI's `render`
// script and react-email's own `email dev` preview use) -- no separate
// story-only fixture data to keep in sync.
export const VerifyEmailStory: Story = {
  name: "Verify Email",
  render: () => <EmailPreview element={<VerifyEmail {...verifyEmailExampleProps} />} />,
};

export const PasswordResetStory: Story = {
  name: "Password Reset",
  render: () => <EmailPreview element={<PasswordReset {...passwordResetExampleProps} />} />,
};

export const MagicLinkStory: Story = {
  name: "Magic Link",
  render: () => <EmailPreview element={<MagicLink {...magicLinkExampleProps} />} />,
};

export const InvitationStory: Story = {
  name: "Invitation",
  render: () => <EmailPreview element={<Invitation {...invitationExampleProps} />} />,
};

export const WelcomeStory: Story = {
  name: "Welcome",
  render: () => <EmailPreview element={<Welcome {...welcomeExampleProps} />} />,
};

export const DownloadLinkStory: Story = {
  name: "Download Link",
  render: () => <EmailPreview element={<DownloadLink {...downloadLinkExampleProps} />} />,
};

export const MemberInvitedStory: Story = {
  name: "Member Invited",
  render: () => <EmailPreview element={<MemberInvited {...memberInvitedExampleProps} />} />,
};

export const PaymentFailedStory: Story = {
  name: "Payment Failed",
  render: () => <EmailPreview element={<PaymentFailed {...paymentFailedExampleProps} />} />,
};

export const SubscriptionExpiringStory: Story = {
  name: "Subscription Expiring",
  render: () => (
    <EmailPreview element={<SubscriptionExpiring {...subscriptionExpiringExampleProps} />} />
  ),
};

export const SubscriptionSuspendedStory: Story = {
  name: "Subscription Suspended",
  render: () => (
    <EmailPreview element={<SubscriptionSuspended {...subscriptionSuspendedExampleProps} />} />
  ),
};

export const TrialEndingStory: Story = {
  name: "Trial Ending",
  render: () => <EmailPreview element={<TrialEnding {...trialEndingExampleProps} />} />,
};

export const TrialExpiredStory: Story = {
  name: "Trial Expired",
  render: () => <EmailPreview element={<TrialExpired {...trialExpiredExampleProps} />} />,
};

export const FileUploadedStory: Story = {
  name: "File Uploaded",
  render: () => <EmailPreview element={<FileUploaded {...fileUploadedExampleProps} />} />,
};
