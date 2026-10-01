import { describe, expect, it } from "vitest";

import { makeStoredFile } from "../../test/appShellFixtures";
import { SCAN_POLL_GIVE_UP_MS, scanPollInterval } from "./fileStatus";

const NOW = Date.parse("2026-09-30T12:00:00Z");

function pendingFor(ms: number) {
  return makeStoredFile(1, {
    virus_scan_result: null,
    updated_at: new Date(NOW - ms).toISOString(),
  });
}

describe("scanPollInterval (review R7)", () => {
  it("does not poll when nothing is waiting for a verdict", () => {
    expect(scanPollInterval([makeStoredFile(1)], NOW)).toBe(false);
    expect(scanPollInterval([], NOW)).toBe(false);
  });

  it("polls quickly at first and backs off as the scan ages", () => {
    expect(scanPollInterval([pendingFor(5_000)], NOW)).toBe(2_000);
    expect(scanPollInterval([pendingFor(60_000)], NOW)).toBe(5_000);
    expect(scanPollInterval([pendingFor(3 * 60_000)], NOW)).toBe(15_000);
  });

  it("gives up on a scan pending longer than the limit", () => {
    expect(scanPollInterval([pendingFor(SCAN_POLL_GIVE_UP_MS + 1)], NOW)).toBe(
      false,
    );
  });

  it("follows the newest pending file, so a fresh upload is not starved by an old one", () => {
    const files = [
      pendingFor(SCAN_POLL_GIVE_UP_MS + 60_000),
      pendingFor(1_000),
    ];
    expect(scanPollInterval(files, NOW)).toBe(2_000);
  });

  it("times a scan from confirmation, so a slow upload still polls (OCT-06)", () => {
    const slowUpload = makeStoredFile(1, {
      virus_scan_result: null,
      created_at: new Date(NOW - 8 * 60_000).toISOString(),
      updated_at: new Date(NOW - 5_000).toISOString(),
    });
    expect(scanPollInterval([slowUpload], NOW)).toBe(2_000);
  });

  it("does not count an infected or failed file as pending", () => {
    const infected = makeStoredFile(1, {
      status: "infected",
      virus_scan_result: "Eicar",
      created_at: new Date(NOW).toISOString(),
    });
    expect(scanPollInterval([infected], NOW)).toBe(false);
  });
});
