import { describe, expect, it } from "vitest";

import { isScanPending, scanRetry, scanRetryDelay } from "./useOrgLogoSrc";

const pending = {
  data: null,
  error: { code: "conflict", message_key: "errors.file_scan_pending" },
};
const infected = {
  data: null,
  error: { code: "conflict", message_key: "errors.file_infected" },
};

describe("logo retry policy (review R7)", () => {
  it("recognises only the scan-pending conflict", () => {
    expect(isScanPending(pending)).toBe(true);
    expect(isScanPending(infected)).toBe(false);
    expect(isScanPending(new Error("network"))).toBe(false);
  });

  it("keeps asking for a couple of minutes while the scan is pending", () => {
    expect(scanRetry(1, pending)).toBe(true);
    expect(scanRetry(23, pending)).toBe(true);
    expect(scanRetry(24, pending)).toBe(false);
    expect(scanRetryDelay(1, pending)).toBe(5_000);
  });

  it("gives up quickly on anything else, an infected logo included", () => {
    expect(scanRetry(2, infected)).toBe(true);
    expect(scanRetry(3, infected)).toBe(false);
  });
});
