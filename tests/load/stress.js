// Capacity: where does one deployment stop keeping its latency budget?
//
// Not a gate. mixed.js and friends assert a budget at a fixed load; this one
// steps the *arrival rate* up (RATES iterations a second, STEP_SECONDS each)
// and reports p95 latency and failures per step, so the knee shows. Run it
// against a deployment, not CI's dev stack:
//
//   k6 run -e BASE_URL=https://app-staging.example -e LOAD_USER_PASSWORD=... \
//          -e RATES=20,40,60,80,100,120 stress.js
//
// Signs in SESSIONS load users once, in setup(), 13 s apart: a deployment
// keeps the real 5/minute sign-in limit, and the per-VU sign-in of mixed.js
// would spend it in seconds. The VUs share those tokens, which a target with
// staging's raised per-user API limit allows. A run must finish within the
// access token's 15 minutes, which bounds RATES x STEP_SECONDS.
//
// The mix is mixed.js's without its 5% fresh sign-ins: 80% paginated reads
// (members, notifications, files: up to 3 pages each), 20% uploads (presign,
// the storage POST, confirm). One iteration is 1 to 3 requests.

import { sleep } from "k6";
import { fetchOrgId, login } from "./lib/auth.js";
import { LOAD_USER_PASSWORD, userEmail } from "./lib/config.js";
import {
  listFiles,
  listMembers,
  listNotifications,
  uploadFile,
} from "./lib/workloads.js";

const RATES = (__ENV.RATES || "20,40,60,80,100,120").split(",").map(Number);
const STEP_SECONDS = parseInt(__ENV.STEP_SECONDS || "60", 10);
const SESSIONS = parseInt(__ENV.SESSIONS || "4", 10);
const WARMUP_SECONDS = (SESSIONS - 1) * 13 + 15;

const scenarios = {};
const thresholds = {};
RATES.forEach((rate, i) => {
  const name = `rate_${rate}`;
  scenarios[name] = {
    executor: "constant-arrival-rate",
    rate,
    timeUnit: "1s",
    duration: `${STEP_SECONDS}s`,
    startTime: `${i * (STEP_SECONDS + 5)}s`,
    preAllocatedVUs: Math.max(20, rate * 2),
    maxVUs: rate * 6,
    tags: { step: String(rate) },
  };
  // Thresholds on the step tag are what make k6 print each step on its own.
  // Generous on purpose: they describe, they do not fail the run.
  thresholds[`http_req_duration{step:${rate}}`] = ["p(95)<60000"];
  thresholds[`http_req_failed{step:${rate}}`] = ["rate<=1"];
  thresholds[`http_reqs{step:${rate}}`] = ["count>=0"];
});

export const options = {
  scenarios,
  thresholds,
  setupTimeout: `${WARMUP_SECONDS + 60}s`,
};

const READS = [listMembers, listNotifications, listFiles];

export function setup() {
  const sessions = [];
  for (let i = 0; i < SESSIONS; i += 1) {
    if (i > 0) {
      sleep(13);
    }
    const token = login(userEmail(i), LOAD_USER_PASSWORD);
    if (!token) {
      throw new Error(`sign-in failed for ${userEmail(i)}`);
    }
    sessions.push({ token, orgId: fetchOrgId(token) });
  }
  return { sessions };
}

export default function (data) {
  const session = data.sessions[__VU % data.sessions.length];
  if (Math.random() < 0.2) {
    uploadFile(session, `${__VU}-${__ITER}`);
  } else {
    READS[Math.floor(Math.random() * READS.length)](session);
  }
}
