import type { StoredFilePublic } from "@forge/api-client";

type StoredFileStatus = StoredFilePublic["status"];

// What the user is shown, which is not the same thing as `status`.
//
// `status` goes to "ready" when the upload is confirmed, and the virus scan
// runs *after* that -- so "ready" spans two states the backend treats very
// differently. Since the download gate began requiring a clean verdict
// (app.modules.storage.scan_gate), a freshly-uploaded file reads "Ready"
// and cannot be downloaded, which is exactly the mismatch this page exists
// to stop showing.
export type DisplayStatus = StoredFileStatus | "scanning";

export function displayStatus(file: StoredFilePublic): DisplayStatus {
  if (file.status === "ready" && file.virus_scan_result === null) {
    return "scanning";
  }
  return file.status;
}

// Review R7. The upload is confirmed long before its scan finishes, and
// nothing told the page when it did: a user who stayed on it watched
// "Scanning" indefinitely after the file became downloadable. So while any
// file on the page awaits a verdict, the list refetches -- quickly at first,
// then less often -- and stops once none do. A scan still pending after five
// minutes stops the polling too, and the page says so, rather than hitting
// the API every fifteen seconds for a scanner that is down.
//
// `at` is when the data was fetched, not "now": the interval and the
// "taking longer" note are both derived from the query's own
// `dataUpdatedAt`, which keeps render pure and keeps the two in agreement.
export const SCAN_POLL_GIVE_UP_MS = 5 * 60_000;

export function scanPollInterval(
  files: readonly StoredFilePublic[],
  at: number,
): number | false {
  const pendingAges = files
    .filter((file) => displayStatus(file) === "scanning")
    .map((file) => at - Date.parse(file.created_at));
  if (pendingAges.length === 0) return false;
  const newest = Math.min(...pendingAges);
  if (newest > SCAN_POLL_GIVE_UP_MS) return false;
  if (newest < 30_000) return 2_000;
  if (newest < 120_000) return 5_000;
  return 15_000;
}
