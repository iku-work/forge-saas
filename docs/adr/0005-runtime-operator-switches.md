# ADR-0005: A database-backed maintenance switch that fails open

## Status

Accepted

**Date:** 2026-08-07

## Context

Three of the things an operator most needs during an incident — stop all
writes, turn one module off, tell every user what is happening — had no home.
`product.config.py` is deploy-time (changing it is a code change, a review and
a deploy), and per-org feature flags are about one customer's entitlements, not
about the platform. So the honest answer to "how do we take the product down
for twenty minutes at 3am" was "push a commit", which is the wrong answer at
3am.

The hard part is not storing a boolean. It is that a switch which decides
whether requests are served at all sits on the request path of *every* request,
and it has three properties that pull against each other:

- **It must be enforced before the router.** A per-route dependency is a rule
  someone forgets to add to the route they write next month.
- **It must be readable cheaply.** A database read per request, on every
  request, to answer "are we in maintenance mode" (a question whose answer is
  "no" essentially always) is a round trip nobody is paying for willingly.
- **It must never lock the operator out.** The mode has to be endable from
  outside itself, by someone who was not already signed in when it started.

And one property that only shows up when you write it down: the switch has its
own dependencies (Redis, Postgres), so it can fail — and it has to have an
answer for what happens then, on the request path, for every request.

## Decision

A singleton row, `product_settings` (`id = 1`, enforced by a
`CHECK (id = 1)` constraint), holding a JSONB document. Well-known keys live in
a typed registry, `backend/app/modules/product_settings/keys.py`; unknown keys
are stored verbatim so a product can ship its own switches without editing the
template.

`MaintenanceModeMiddleware`
(`backend/app/middleware/maintenance.py`) enforces it, and three specifics
matter:

1. **It is registered first in `app.main`, which makes it innermost.**
   Starlette applies middleware in reverse registration order, so it runs after
   `LocaleMiddleware` and `RequestIDMiddleware` (the 503 carries a request id
   and is phrased in the caller's language) and inside `SecurityHeadersMiddleware`
   and CORS (the 503 is a response a browser will let the app read). Exception
   handlers live inside the user middleware stack, so it builds its
   `JSONResponse` by hand rather than raising a `DomainError`.

2. **The exemption list is the design.** `/health*`, `/metrics`,
   `/openapi.json`, `/api/v1/config`, `/api/v1/admin/*`, the
   login/refresh/logout/2FA endpoints, and every `OPTIONS` preflight answer
   normally with maintenance mode on.

3. **It fails open.** If the settings cannot be read — Redis unreachable *and*
   the database unreachable, or `app.state.redis` not yet set — the middleware
   logs `maintenance.settings_unreadable` at WARNING and lets the request
   through.

Reads go through Redis with a 60s TTL (`app.core.cache`); writes bust the key
twice, once in `ProductSettingsService._store` and again in the admin router
after `session.commit()`.

## Consequences

**What is now true.** Maintenance mode, a global banner, per-module kill
switches and `signup_disabled` are all one PATCH away, audited as
`elevated`-severity events with a key-level diff, and reversible from a `curl`
with a superadmin token or a two-statement `psql` session when the admin UI is
what is broken (`docs/runbooks/maintenance-mode.md`).

**What got worse:**

- **Every non-admin route issues one more SQL statement on a cold cache.** In
  production that is one indexed primary-key lookup per minute per deployment,
  not per request — but it is real, and it is why twelve budgets in
  `tests/test_query_budgets.py` went up by one. The three admin budgets did
  not, which is a standing check on the `/api/v1/admin/*` exemption: if one
  ever needs `+1` for this reason, the exemption has broken.
- **`GET /api/v1/config` is no longer purely deploy-time**, so its
  `Cache-Control` came down from `max-age=300` to `max-age=60`. Nothing can
  invalidate a browser's own HTTP cache, so that number *is* the worst-case
  delay before an already-open tab sees a banner. Web and mobile poll the
  endpoint on the same 60s interval for the same reason.
- **Failing open means a total infrastructure outage silently disables
  maintenance mode.** This is the cost of the third decision above and it is
  deliberate, not a gap: see Alternatives.
- **A second thing can now be down.** Redis being unreachable turns the
  cached read into a database read per request. Bounded (one indexed lookup)
  but worth knowing about when the connection pool is the thing under
  investigation.
- **Only three modules are kill-switchable** — `billing`, `file_storage`,
  `notifications`. `gdpr` mounts on `/api/v1/me` (shared with auth's own
  profile routes and T-262's support context) and `feature_flags` under
  tenancy's `/orgs`, so killing either by path prefix would take a user's
  ability to read their own account with it. Those are rejected with a 422
  rather than accepted as switches that do nothing.

## Alternatives considered

**Fail closed** (503 everything when the settings cannot be read). Rejected,
and this is the decision most worth arguing with. Failing closed is the right
instinct for an authorization check, where the cost of wrongly allowing is
unbounded; it is the wrong one here, because the cost of wrongly *blocking* is
a total outage, and the trigger is "Redis and Postgres are both unreachable" —
a moment when the system is already degraded and an operator is already
diagnosing it. Failing closed converts a degraded system into a dead one at
exactly the wrong time, and it does so on the path an operator would use to
find out why. The blast radius of failing open is bounded and specific:
maintenance mode stops being enforced, having been an operator convenience
rather than a security control at any point. Nothing in the exemption list
grants access — `/api/v1/admin/*` is still behind `require_superadmin` — so
failing open cannot leak anything either.

**An environment variable plus a redeploy.** The status quo, and the thing this
replaces. It is genuinely simpler and has no request-path cost. It loses on the
one axis that matters: the time between deciding to stop the bleeding and the
bleeding stopping is a CI run and a rolling deploy.

**A per-route dependency instead of middleware.** Would let each route opt in
with its own semantics, and would cost nothing on exempt routes. Rejected
because "the route somebody writes next month" is not covered by it, and a
maintenance switch with a hole in it is worse than none — an operator believes
the product is down when it is not.

**A file on disk, or a config service.** A file does not exist on a
multi-replica deployment without a shared volume, and drifts per replica. A
config service (Consul, etcd, LaunchDarkly) is a new dependency on the request
path of every request, for one row — and this template already has both a
database and Redis, which is exactly the shape needed.

**Caching in process memory instead of Redis.** Cheaper (no network hop) but
each replica would expire on its own schedule, so "maintenance mode is on"
would be true on some replicas and false on others for up to a TTL. A user
hitting a load balancer would see the window flicker. Redis makes the bust
global and immediate.
