import http from "k6/http";
import { check } from "k6";
import { BASE_URL } from "./config.js";

// Returns the bearer access token, or null if login failed (caller decides
// whether that's a hard stop -- login-burst.js keeps going regardless,
// everything else bails out of the iteration).
export function login(email, password) {
  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ email, password }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "auth_login" } }
  );
  const ok = check(res, { "login succeeded": (r) => r.status === 200 });
  return ok ? res.json("data.access_token") : null;
}

// Every seeded load-test account belongs to exactly one org (the "Load Test
// Org" backend/app/core/seed_load.py creates), so the first row is always
// the right one -- same call shape a real client makes right after login to
// discover which org(s) it can act as.
export function fetchOrgId(token) {
  const res = http.get(`${BASE_URL}/orgs`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { name: "list_my_orgs" },
  });
  const ok = check(res, { "list orgs succeeded": (r) => r.status === 200 });
  return ok ? res.json("data.0.id") : null;
}

export function authHeaders(token, orgId) {
  const headers = { Authorization: `Bearer ${token}` };
  if (orgId) {
    headers["X-Org-ID"] = orgId;
  }
  return headers;
}
