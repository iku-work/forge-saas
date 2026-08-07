import { describe, expect, it, vi } from "vitest";

import {
  formatBytes,
  formatDate,
  formatRelativeTime,
  supportMailtoUrl,
} from "./index";

describe("formatRelativeTime", () => {
  it("formats a past instant relative to now", () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(twoHoursAgo)).toBe("2 hours ago");
  });

  it("formats a future instant relative to now", () => {
    const inThreeDays = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(inThreeDays)).toBe("in 3 days");
  });

  // Regression test for a real crash found verifying the iOS app (T-160):
  // Expo Go's bundled Hermes has no Intl.RelativeTimeFormat and throws
  // "Cannot read property 'prototype' of undefined" on `new
  // Intl.RelativeTimeFormat(...)`, which crashed the whole Notifications
  // screen. Node's Intl always has it, so this simulates the gap directly
  // rather than relying on a specific engine.
  it("falls back to a plain-English string when Intl.RelativeTimeFormat is unavailable", () => {
    const mutableIntl = Intl as { RelativeTimeFormat: typeof Intl.RelativeTimeFormat | undefined };
    const original = mutableIntl.RelativeTimeFormat;
    mutableIntl.RelativeTimeFormat = undefined;
    try {
      const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
      const inThreeDays = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString();
      expect(() => formatRelativeTime(twoHoursAgo)).not.toThrow();
      expect(formatRelativeTime(twoHoursAgo)).toBe("2 hours ago");
      expect(formatRelativeTime(inThreeDays)).toBe("in 3 days");
    } finally {
      mutableIntl.RelativeTimeFormat = original;
    }
  });

  it("falls back gracefully when the constructor itself throws", () => {
    const mutableIntl = Intl as { RelativeTimeFormat: typeof Intl.RelativeTimeFormat };
    const original = mutableIntl.RelativeTimeFormat;
    mutableIntl.RelativeTimeFormat = vi.fn(() => {
      throw new TypeError("Cannot read property 'prototype' of undefined");
    }) as unknown as typeof Intl.RelativeTimeFormat;
    try {
      const oneMinuteAgo = new Date(Date.now() - 60 * 1000).toISOString();
      expect(() => formatRelativeTime(oneMinuteAgo)).not.toThrow();
    } finally {
      mutableIntl.RelativeTimeFormat = original;
    }
  });
});

describe("formatDate", () => {
  it("formats an ISO string using the given locale", () => {
    expect(formatDate("2026-01-15T00:00:00.000Z")).toContain("2026");
  });
});

describe("formatBytes", () => {
  it("formats bytes into human-readable units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
  });
});

describe("supportMailtoUrl", () => {
  const ctx = {
    userId: "u-1",
    email: "jane@example.com",
    orgId: "o-1",
    orgName: "Acme Inc",
    plan: "pro",
    subscriptionStatus: "active",
    locale: "en",
  };

  function bodyOf(url: string): string {
    const query = url.slice(url.indexOf("?") + 1);
    return new URLSearchParams(query).get("body") ?? "";
  }

  it("appends the user's context to the mail body", () => {
    const body = bodyOf(supportMailtoUrl("mailto:support@example.com", ctx));

    expect(body).toContain("jane@example.com");
    expect(body).toContain("Acme Inc (o-1)");
    expect(body).toContain("Plan: pro (active)");
    expect(body).toContain("Locale: en");
  });

  it("reports a missing subscription rather than an empty field", () => {
    const body = bodyOf(
      supportMailtoUrl("mailto:support@example.com", {
        ...ctx,
        plan: null,
        subscriptionStatus: null,
      }),
    );

    expect(body).toContain("Plan: none (no subscription)");
  });

  it("adds the given subject but never overwrites one already in the URL", () => {
    const added = supportMailtoUrl("mailto:support@example.com", ctx, {
      subject: "Help",
    });
    const preserved = supportMailtoUrl(
      "mailto:support@example.com?subject=Fixed",
      ctx,
      { subject: "Help" },
    );

    expect(new URLSearchParams(added.split("?")[1]).get("subject")).toBe("Help");
    expect(new URLSearchParams(preserved.split("?")[1]).get("subject")).toBe(
      "Fixed",
    );
  });

  it("percent-encodes spaces so mail clients don't render literal plus signs", () => {
    const url = supportMailtoUrl("mailto:support@example.com", ctx, {
      subject: "Need help",
    });

    expect(url).toContain("Need%20help");
    expect(url).not.toContain("+");
  });

  it("leaves a non-mailto URL, and a null context, untouched", () => {
    expect(supportMailtoUrl("https://help.example.com", ctx)).toBe(
      "https://help.example.com",
    );
    expect(supportMailtoUrl("mailto:support@example.com", null)).toBe(
      "mailto:support@example.com",
    );
  });
});
