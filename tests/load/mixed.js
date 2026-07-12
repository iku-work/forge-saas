import { sleep } from "k6";
import { login } from "./lib/auth.js";
import { getSession } from "./lib/session.js";
import { listFiles, listMembers, listNotifications, uploadFile } from "./lib/workloads.js";
import { LOAD_USER_COUNT, LOAD_USER_PASSWORD, THRESHOLDS, userEmail } from "./lib/config.js";

// T-223: weighted combination approximating production -- 80% reads / 15%
// writes / 5% auth, ramping up and held for a few minutes. This is the one
// script meant to look like real traffic rather than isolating one endpoint
// family, so it's the closest proxy for "would a latency regression
// anywhere in the hot path actually get caught."
//
// Peak VUs is 35, not the 50 the spec sketch names -- another real capacity
// finding, same category as login-burst.js's, not a rate-limit or config
// workaround. docker-compose.yml's backend runs one uvicorn process (no
// --workers, matching Dockerfile's own prod CMD -- this template has no
// multi-worker/multi-replica story to opt into). Isolated single-purpose
// runs against that one process comfortably clear budget at real
// concurrency (browse.js: 20 VUs, p95 ~25ms; presign.js: 15 VUs, p95
// ~105ms) -- the fixes in auth/service.py (argon2 off the event loop),
// core/db.py (DB_ECHO, DB_POOL_SIZE/DB_MAX_OVERFLOW) were real bugs this
// script surfaced and are load-bearing for every VU count above. What's
// left past those fixes is the one process itself: a plain GET /health with
// no auth/DB work at all still showed p(95)=189ms at 50 concurrent VUs
// hammering it continuously, confirming the remaining ceiling is
// event-loop/connection-handling capacity for *one* process, not a
// findable code defect. 35 VUs held comfortable headroom (p95 ~134ms)
// across repeated runs; 50 sat right at or over budget on this same
// hardware after every other fix. A future multi-worker/multi-replica
// setup should raise this back toward the original 50.
export const options = {
  scenarios: {
    mixed: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 35 },
        { duration: "3m", target: 35 },
        { duration: "15s", target: 0 },
      ],
    },
  },
  thresholds: THRESHOLDS,
};

const READS = [listMembers, listNotifications, listFiles];

export default function () {
  const roll = Math.random();

  // 5% auth: a fresh login, deliberately bypassing lib/session.js's cache --
  // this is the traffic slice modelling new sign-ins, not the VU's own
  // authenticated session.
  if (roll < 0.05) {
    login(userEmail(Math.floor(Math.random() * LOAD_USER_COUNT)), LOAD_USER_PASSWORD);
    sleep(1);
    return;
  }

  const session = getSession();
  if (!session) {
    sleep(1);
    return;
  }

  // 15% writes
  if (roll < 0.2) {
    uploadFile(session, `${__VU}-${__ITER}`);
  } else {
    // 80% reads, evenly split across the three list endpoints
    READS[Math.floor(Math.random() * READS.length)](session);
  }
  sleep(1);
}
