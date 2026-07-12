import { sleep } from "k6";
import { getSession } from "./lib/session.js";
import { listFiles, listMembers, listNotifications } from "./lib/workloads.js";
import { THRESHOLDS } from "./lib/config.js";

// T-223: authenticated list pagination -- members, notifications, files,
// cursor-follows-cursor. Each VU logs in once (lib/session.js caches for
// the VU's lifetime) as one of the 50 real accounts
// backend/app/core/seed_load.py seeds into "Load Test Org", which has 1k
// members / 10k notifications (200/user) / 300 files to actually page
// through -- an empty or single-page collection wouldn't exercise the
// `after` cursor at all.
//
// LOAD_USER_COUNT (50) comfortably covers this scenario's 20-VU peak, so
// each VU keeps one account to itself for the whole run (lib/session.js).
// The sleeps below model a human actually reading each list before moving
// to the next one, instead of firing all three simultaneously -- realism,
// not a rate-limit workaround: backend/.env.loadtest already raises
// API_RATE_LIMIT for this stack (see that file's comment for why 100/minute,
// a single budget shared across every api_limit-gated endpoint rather than
// one per endpoint, is a real ceiling k6's request pace can run into even
// though a human reading three lists never would).
export const options = {
  scenarios: {
    browse: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "10s", target: 20 },
        { duration: "30s", target: 20 },
        { duration: "10s", target: 0 },
      ],
    },
  },
  thresholds: THRESHOLDS,
};

export default function () {
  const session = getSession();
  if (!session) {
    sleep(2);
    return;
  }
  listMembers(session);
  sleep(2);
  listNotifications(session);
  sleep(2);
  listFiles(session);
  sleep(2);
}
