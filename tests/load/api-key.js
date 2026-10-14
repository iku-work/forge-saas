import http from "k6/http";
import { check, fail, sleep } from "k6";
import { login, fetchOrgId, authHeaders } from "./lib/auth.js";
import {
  BASE_URL,
  LOAD_USER_PASSWORD,
  THRESHOLDS,
  userEmail,
} from "./lib/config.js";

// T-350: the API-key hot path, which is a *different* hot path from the JWT
// one every other script in this directory exercises.
//
// A session token is verified with a signature check and nothing else. A key
// (T-310/T-311) is verified with a Redis or database lookup by prefix plus a
// constant-time digest comparison of the secret -- per request, on the way in,
// before any handler runs. That is exactly the shape of path the
// argon2-on-the-event-loop bug T-223 found was hiding in, so it gets its own
// script and its own budget rather than riding along inside mixed.js where a
// regression would be averaged away against reads that never touch it.
//
// **Requires `public_api` enabled.** The module ships off, so a default stack
// answers 404 here. Boot this one with the phase4 fixture, the same way CI's
// module-matrix job does:
//
//   cp backend/tests/fixtures/module_configs/phase4.py backend/product/config.py
//
// setup() below fails loudly rather than letting the run report a green
// budget it never measured.
export const options = {
  scenarios: {
    api_key: {
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

// One key for the whole run, minted once. k6 hands setup()'s return value to
// every VU, so this models what an integrator actually does -- a long-lived
// credential used by many concurrent callers -- rather than a key per VU,
// which would measure key *creation* and leave 20 rows behind.
export function setup() {
  const token = login(userEmail(0), LOAD_USER_PASSWORD);
  if (!token) {
    fail(
      "could not log in to mint an API key -- is the stack seeded (make seed-load)?",
    );
  }
  const orgId = fetchOrgId(token);
  if (!orgId) {
    fail(
      "the load-test user has no organization -- run `make seed` before `make seed-load`",
    );
  }

  const created = http.post(
    `${BASE_URL}/api-keys`,
    JSON.stringify({
      name: `k6 load ${Date.now()}`,
      scopes: ["orgs:read", "members:read"],
    }),
    {
      // authHeaders() carries no Content-Type -- every other script's calls
      // are GETs. This one posts a body.
      headers: {
        ...authHeaders(token, orgId),
        "Content-Type": "application/json",
      },
      tags: { name: "create_api_key" },
    },
  );
  if (created.status === 404) {
    fail(
      "POST /api-keys is 404 -- `public_api` is disabled on this stack (see this file's header)",
    );
  }
  if (created.status !== 201 && created.status !== 200) {
    fail(`could not create an API key: ${created.status} ${created.body}`);
  }

  // The secret exists in exactly this one response and is never readable
  // again (ApiKeyCreated) -- so it is carried through setup()'s return value
  // rather than re-fetched per VU.
  const key = created.json("data.key");
  const keyId = created.json("data.api_key.id");
  if (!key) {
    fail("the create response carried no key");
  }

  // T-312's per-key override, raised deliberately. The point of this script
  // is the cost of *authenticating* a key; the default budget (600/min) would
  // measure the rate limiter refusing us instead, and http_req_failed would
  // report a broken API rather than a throttled test. The override is a PATCH
  // field only: sent on create it is silently dropped, which is how 45% of
  // this script's requests came back 429 every night until 2026-10-14.
  const raised = http.patch(
    `${BASE_URL}/api-keys/${keyId}`,
    JSON.stringify({ rate_limit_per_minute: 100000 }),
    {
      headers: {
        ...authHeaders(token, orgId),
        "Content-Type": "application/json",
      },
      tags: { name: "raise_key_rate_limit" },
    },
  );
  if (raised.status !== 200 || raised.json("data.rate_limit_per_minute") !== 100000) {
    fail(`could not raise the key's rate limit: ${raised.status} ${raised.body}`);
  }
  return { key, keyId, orgId, token };
}

// A key presents on the same `Authorization: Bearer` header a session uses --
// the two are told apart by the `forge_sk_` prefix, not by a second header
// (see app/modules/api_keys/service.py::looks_like_api_key). Sending it the
// way an integrator would is the whole point: a bespoke header here would
// exercise a path nothing in production takes.
function keyHeaders(data) {
  return {
    Authorization: `Bearer ${data.key}`,
    "X-Org-ID": data.orgId,
    "Content-Type": "application/json",
  };
}

export default function (data) {
  const headers = keyHeaders(data);

  const org = http.get(`${BASE_URL}/orgs/${data.orgId}`, {
    headers,
    tags: { name: "key_get_org" },
  });
  check(org, { "key_get_org succeeded": (r) => r.status === 200 });
  sleep(1);

  // The list endpoint, paged, because a key-authenticated integration reading
  // a members list is the realistic workload and it is also where a per-request
  // auth cost compounds: every page is a fresh lookup and digest comparison.
  let after = null;
  for (let page = 0; page < 3; page++) {
    const qs = after ? `?after=${after}&limit=20` : `?limit=20`;
    const res = http.get(`${BASE_URL}/orgs/${data.orgId}/members${qs}`, {
      headers,
      tags: { name: "key_list_members" },
    });
    if (
      !check(res, { "key_list_members succeeded": (r) => r.status === 200 })
    ) {
      break;
    }
    const cursor = res.json("meta.next_cursor");
    if (!cursor) {
      break;
    }
    after = cursor;
  }
  sleep(1);
}

// Revoked rather than left behind: this script is run repeatedly against the
// same seeded stack, and a live key per run accumulates credentials in a table
// whose whole point is that they authenticate.
export function teardown(data) {
  if (!data || !data.keyId) {
    return;
  }
  http.post(`${BASE_URL}/api-keys/${data.keyId}/revoke`, null, {
    headers: authHeaders(data.token, data.orgId),
    tags: { name: "revoke_api_key" },
  });
}
