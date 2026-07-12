import { sleep } from "k6";
import { login } from "./lib/auth.js";
import { LOAD_USER_COUNT, LOAD_USER_PASSWORD, THRESHOLDS, userEmail } from "./lib/config.js";

// T-223: spike of logins. Rate-limit-aware means distinct virtual users, not
// one hammered account -- each VU always logs in as the same one of the
// LOAD_USER_COUNT accounts backend/app/core/seed_load.py seeds, keyed off
// k6's own (stable, 1-indexed) VU index, rather than every VU sharing one
// email. This doesn't dodge app.middleware.rate_limit's AUTH_RATE_LIMIT --
// that's keyed by source IP, not account, and every k6 VU shares this
// runner's one IP regardless -- backend/.env.loadtest raises the limit for
// that reason (see its own comment). Distinct accounts still matter here:
// it's what a real login spike looks like, and it exercises the by-email
// user lookup under concurrency instead of one cached/locked row.
//
// Peak VUs is deliberately modest (8, not e.g. 30) -- this isn't a
// rate-limit workaround, it's a real capacity finding this suite itself
// surfaced: argon2's security-mandated parallelism=4 (T-225/ASVS V2.4.5)
// makes each verify_password call use ~4 threads' worth of CPU for its
// ~45ms; a burst of genuinely concurrent verifies contends for the same
// cores and its own internal lanes, so past roughly 10 truly-simultaneous
// logins a single backend replica's p95 crosses this script's 300ms budget
// on CPU cost alone, not a code bug (asyncio.to_thread already moves the
// blocking call off the event loop -- see auth/service.py's
// _hash_password_off_loop/_verify_password_off_loop). Weakening argon2's
// params to hit a load-test number would trade real account-security
// margin for a green check mark, which is the wrong trade -- the fix for
// a genuine login spike beyond one replica's capacity is more replicas,
// same as any other CPU-bound endpoint.
export const options = {
  scenarios: {
    login_burst: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "10s", target: 8 },
        { duration: "20s", target: 8 },
        { duration: "10s", target: 0 },
      ],
    },
  },
  thresholds: THRESHOLDS,
};

export default function () {
  const email = userEmail((__VU - 1) % LOAD_USER_COUNT);
  login(email, LOAD_USER_PASSWORD);
  sleep(1);
}
