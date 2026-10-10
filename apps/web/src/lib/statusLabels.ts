import type { StatusTone } from "@forge/ui";

type Translate = (key: string) => string;

// Stripe's subscription states. Anything else (a state Stripe adds later)
// shows its raw word rather than a missing translation.
const SUBSCRIPTION_STATUSES = new Set([
  "active",
  "trialing",
  "past_due",
  "canceled",
  "unpaid",
  "incomplete",
  "incomplete_expired",
  "paused",
]);

export function subscriptionStatusLabel(t: Translate, status: string): string {
  return SUBSCRIPTION_STATUSES.has(status)
    ? t(`common.subscription_status.${status}`)
    : status;
}

/** Stripe's subscription states, by how much attention they need. */
export function subscriptionTone(status: string): StatusTone {
  if (status === "active") return "active";
  if (status === "trialing") return "pending";
  if (["past_due", "unpaid", "incomplete_expired"].includes(status)) {
    return "danger";
  }
  return "neutral";
}

/** The audit feed's two severities ("elevated" marks admin writes). */
export function auditSeverityLabel(t: Translate, severity: string): string {
  return severity === "info" || severity === "elevated"
    ? t(`common.admin.audit.severity_${severity}`)
    : severity;
}
