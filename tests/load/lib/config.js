import http from "k6/http";

// T-223: every script targets the backend directly (not through Caddy) --
// same host:port docker-compose.yml publishes and every CI job's own
// "wait for backend" step already curls (http://localhost:8000/health).
export const BASE_URL = `${__ENV.BASE_URL || "http://localhost:8000"}/api/v1`;

// Must match backend/app/core/seed_load.py's LOAD_USER_COUNT -- that script
// creates load-user-0..{LOAD_USER_COUNT-1}@example.com as real,
// login-capable members of the seeded "Load Test Org". Overridable via
// __ENV in case a future run seeds a different pool size.
export const LOAD_USER_COUNT = parseInt(__ENV.LOAD_USER_COUNT || "50", 10);
export const LOAD_USER_PASSWORD = "LoadTestPass123!";

export function userEmail(i) {
  return `load-user-${i}@example.com`;
}

// T-223: "p95 < 300ms and error rate < 1% are asserted, not eyeballed" --
// identical in every script in this directory (enforced by a CI grep, see
// tests/load/README.md).
export const THRESHOLDS = {
  http_req_duration: ["p(95)<300"],
  http_req_failed: ["rate<0.01"],
};

// k6 only counts a response toward http_req_failed on a network/protocol
// error by default -- a 429 or 500 response that completed normally isn't
// "failed" until a response callback says so. Without this, the
// http_req_failed threshold above would silently never fire no matter how
// many error responses the API actually returns. Applies to every request
// made by whichever script imports this module (k6 scopes the callback to
// the current VU's init context). 204 covers the S3 POST-policy upload leg
// in presign.js/mixed.js (MinIO's typical success response for that call).
http.setResponseCallback(http.expectedStatuses(200, 201, 204));
