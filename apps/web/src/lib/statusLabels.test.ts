import { describe, expect, it } from "vitest";

import { t } from "@forge/i18n";

import {
  auditSeverityLabel,
  subscriptionStatusLabel,
  subscriptionTone,
} from "./statusLabels";

const en = (key: string): string => t(key, {}, "en");

describe("admin status labels", () => {
  it("translates Stripe's subscription states", () => {
    expect(subscriptionStatusLabel(en, "past_due")).toBe("Past due");
    expect(subscriptionStatusLabel(en, "trialing")).toBe("Trial");
  });

  it("shows a state Stripe adds later as its own word, not a missing key", () => {
    expect(subscriptionStatusLabel(en, "something_new")).toBe("something_new");
  });

  it("tones the states that need attention as danger", () => {
    expect(subscriptionTone("past_due")).toBe("danger");
    expect(subscriptionTone("active")).toBe("active");
    expect(subscriptionTone("canceled")).toBe("neutral");
  });

  it("translates the two audit severities", () => {
    expect(auditSeverityLabel(en, "elevated")).toBe("Elevated");
    expect(auditSeverityLabel(en, "other")).toBe("other");
  });
});
