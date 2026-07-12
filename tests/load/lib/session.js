import { login, fetchOrgId } from "./auth.js";
import { LOAD_USER_COUNT, LOAD_USER_PASSWORD, userEmail } from "./config.js";

// Module-scope state is per-VU in k6 (each VU gets its own isolated JS
// runtime), so this caches for the lifetime of one VU rather than logging
// in again on every iteration -- realistic (a real client keeps its session
// across many requests) and keeps browse.js/presign.js/mixed.js from
// contributing to auth_login volume at all past their first iteration.
// login-burst.js deliberately does NOT use this -- it wants a fresh login
// every iteration and imports lib/auth.js's login() directly instead.
let cached = null;

export function getSession() {
  if (cached) {
    return cached;
  }
  // __VU is 1-indexed and stable for a VU's whole lifetime under k6's
  // ramping-vus executor, so this deterministically spreads VUs across the
  // seeded pool (backend/app/core/seed_load.py) without needing setup() to
  // hand out assignments.
  const email = userEmail((__VU - 1) % LOAD_USER_COUNT);
  const token = login(email, LOAD_USER_PASSWORD);
  if (!token) {
    return null;
  }
  const orgId = fetchOrgId(token);
  if (!orgId) {
    return null;
  }
  cached = { token, orgId };
  return cached;
}
