# Load tests (T-223)

k6 scripts that model realistic traffic against a real backend and assert
budgets instead of eyeballing a dashboard:

```javascript
thresholds: {
  http_req_duration: ["p(95)<300"],
  http_req_failed:   ["rate<0.01"],
}
```

Every script in this directory (`login-burst.js`, `browse.js`, `presign.js`,
`mixed.js`, `api-key.js`) declares the identical two thresholds above -- checked by a CI
grep (see `.github/workflows/load-test.yml`) so a script can't quietly drop
the budget it's supposed to be asserting.

| Script            | Models                                                              |
| ------------------ | -------------------------------------------------------------------- |
| `login-burst.js`  | A spike of logins, distinct accounts per VU, not one hammered login |
| `browse.js`       | Authenticated list pagination: members, notifications, files        |
| `presign.js`      | File presign -> real upload to MinIO -> confirm, the full round trip |
| `mixed.js`        | 80% reads / 15% writes / 5% auth, ramping 0 -> 35 VUs, held 3 min    |
| `api-key.js`      | The **key**-authenticated read path (T-350) -- a different hot path from the four above |

`api-key.js` is the odd one out twice over. It is the only script whose
credential is not a session token, and it is the only one that needs a module
turned on: `public_api` ships off, so a default stack answers 404 and its
`setup()` says so rather than reporting a budget it never measured. The
nightly workflow copies `backend/tests/fixtures/module_configs/phase4.py`
over `backend/product/config.py` before booting, which is the same
configuration CI's `module-matrix` job already proves boots and migrates.

It earns its own script rather than a branch inside `mixed.js` because key
auth is genuinely a different code path: a session token costs one signature
check, while a key costs a lookup by prefix plus a constant-time digest
comparison **per request**, before any handler runs. That is the shape of
path T-223 found the argon2-on-the-event-loop bug hiding in, and averaging it
into a mixed workload is how a regression there goes unnoticed. It also mints
its key with `rate_limit_per_minute: 100000` on purpose -- with T-312's
default budget in place the run would measure the rate limiter refusing it,
not the cost of authenticating.

`lib/` holds the shared helpers every script imports (login, session
caching, the list/upload workloads) -- see that directory's own files for
what each one does; there's nothing script-specific left to explain beyond
this table.

`mixed.js`'s peak is 35 VUs, not the 50 sketched in the original task --
`docker-compose.yml`'s backend runs one uvicorn process (matches `Dockerfile`'s
own prod `CMD`, no `--workers`; this template has no multi-worker/multi-replica
story to opt into yet), and 50 fully-concurrent VUs sits right at or over this
one process's own capacity ceiling on ordinary dev hardware -- confirmed with a
plain `GET /health` (no auth, no DB) showing the same ceiling. See `mixed.js`'s
own header comment for the full investigation, including the three real bugs
found and fixed along the way (argon2 blocking the event loop, SQL echo
logging under load, an undersized DB connection pool) before concluding the
rest was process capacity, not code.

## Why nightly, not per-PR

Per-PR CI runs on shared, differently-loaded GitHub runners -- the same
`docker compose` stack can be 3x slower on a noisy runner than a quiet one
for reasons that have nothing to do with the code in the PR. A `p(95)<300ms`
budget on that kind of runner produces flaky failures on unrelated PRs (or,
worse, gets loosened until it stops catching anything). Running nightly on
its own dedicated runner class instead means a red run means something
regressed, not that the runner was busy.

This also keeps the ~4-minute `mixed.js` stage (plus the other three scripts
and a 1k-member/10k-notification seed) off the critical path of every PR --
nobody wants to wait on it to merge a docs fix.

## Running locally

```sh
cp backend/.env.loadtest backend/.env   # see that file's header for why
make dev                                # boots the stack on the env above
make load-test                          # seeds the load-test org, runs all four scripts
```

The first step matters, not just convenience: `backend/.env.loadtest` raises
`AUTH_RATE_LIMIT`/`API_RATE_LIMIT` well past their production defaults and
turns off `DB_ECHO`/raises `DB_POOL_SIZE` -- without it, `login-burst.js` and
`browse.js` fail on a wall of 429s (this stack's own traffic tripping limits
sized for real client behavior, not a k6 VU's tighter loop) and `mixed.js`
blows its latency budget on debug-logging overhead and DB-pool queuing that
production doesn't have either. See `backend/.env.loadtest`'s own header
comment for the full reasoning behind each override -- every one of them was
found by actually running these scripts against the unmodified defaults
first, not decided upfront.

If you already have a `make dev` stack running on your own `backend/.env`,
either swap envs and recreate the `backend` container (`cp
backend/.env.loadtest backend/.env && docker compose up -d backend`) or
expect the failures above -- don't loosen the thresholds instead, that's
exactly the noise nightly-not-per-PR (below) exists to keep out.

`make load-test` runs `make seed-load` first (idempotent -- safe to re-run;
see `backend/app/core/seed_load.py`'s own docstring for what it seeds and
why), then each script via `docker compose run --rm k6 ...`, one script at a
time so a budget failure in one doesn't obscure whether the others also
failed. Results land as JSON summaries in `tests/load/results/`, gitignored
-- `RESULTS.md` in this directory is the durable trend log; the nightly CI
job appends one line to it per run (see that workflow for the exact format).

Login-capable accounts: `load-user-0@example.com` .. `load-user-49@example.com`,
password `LoadTestPass123!`, all members of "Load Test Org". Everything else
`seed-load` creates (950 filler members, 10k notifications, 300 files) exists
purely to give the list endpoints something to paginate through and is never
logged in as.
