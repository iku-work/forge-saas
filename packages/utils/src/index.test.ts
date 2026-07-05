import { describe, expect, it, vi } from "vitest";

import { formatBytes, formatDate, formatRelativeTime } from "./index";

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
