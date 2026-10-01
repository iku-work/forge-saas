import { describe, expect, it } from "vitest";

import { avatarRetry, avatarRetryDelay, isScanPending } from "./useAvatarSrc";

const pending = {
  data: null,
  error: { code: "conflict", message_key: "errors.file_scan_pending" },
};
const infected = {
  data: null,
  error: { code: "conflict", message_key: "errors.file_infected" },
};

describe("avatar retry policy (review R7)", () => {
  it("recognises only the scan-pending conflict", () => {
    expect(isScanPending(pending)).toBe(true);
    expect(isScanPending(infected)).toBe(false);
    expect(isScanPending(new Error("network"))).toBe(false);
  });

  it("keeps asking for a couple of minutes while the scan is pending", () => {
    expect(avatarRetry(1, pending)).toBe(true);
    expect(avatarRetry(23, pending)).toBe(true);
    expect(avatarRetry(24, pending)).toBe(false);
    expect(avatarRetryDelay(1, pending)).toBe(5_000);
  });

  it("gives up quickly on anything else, an infected avatar included", () => {
    expect(avatarRetry(2, infected)).toBe(true);
    expect(avatarRetry(3, infected)).toBe(false);
  });
});
