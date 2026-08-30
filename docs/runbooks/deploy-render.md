# Deploying to Render

The `deploy` job in `.github/workflows/ci.yml` targets [Render](https://render.com)
and is dormant until the secrets below are configured on the repo
(Settings → Secrets and variables → Actions). Until then, pushes to `main`
build and push the image but the `deploy` job fails on its first step.

## Trigger

Standing this template up on Render for the first time, or re-creating the
deployment after the Render side was torn down. One-time setup: once the five
secrets in step 3 exist, every push to `main` deploys itself and nothing here
needs doing again.

Not this runbook: a failing deploy on an already-configured repo (that is the
`deploy` job's own logs and the Rollback section below), or the self-hosted
`docker-compose.prod.yml` path, which shares nothing with this except the
image.

## Pre-checks

- A Render account, and a decision about which plan the Postgres instance is
  on — retention and PITR availability follow from it, and
  `backup-restore.md` assumes you know which you have.
- At least one successful `build` run on `main`, so `ghcr.io/<owner>/<repo>`
  actually has an image to point the services at. Creating the services first
  works; they just sit unhealthy until it does.
- The domain you intend to serve from, with DNS you can change — `PROD_DOMAIN`
  is used by the post-deploy smoke test, so a wrong value fails the deploy
  after it has already shipped.
- A production `backend/.env` prepared (field list in `backend/.env.example`).
  `Settings._enforce_startup_invariants` refuses to boot outside dev without
  TLS on `DATABASE_URL`/`REDIS_URL` and a `PASSWORD_PEPPER`, so a `.env` that
  works locally is not one that works here.

## Steps

### 1. Services to create on Render

- A **Web Service**, deploy method "Existing image", pointing at
  `ghcr.io/<owner>/<repo>` (private packages need a Registry Credential —
  see below).
- A **Background Worker**, same image, start command
  `arq app.workers.main.WorkerSettings`.
- A **PostgreSQL** instance and a **Key Value (Redis)** instance.

On the Web Service:
- **Health Check Path**: `/health/ready`
- **Pre-Deploy Command** (optional, defense in depth alongside CI's own
  migration step): `alembic upgrade head`

### 2. Registry credential (only if the GHCR package is private)

Dashboard → Account Settings → Registry Credentials → add a GitHub PAT with
`read:packages` scope, then attach it to both services.

### 3. GitHub Actions secrets to set

| Secret | Where to get it |
|---|---|
| `RENDER_API_KEY` | Dashboard → Account Settings → API Keys |
| `RENDER_BACKEND_SERVICE_ID` | Web service page URL: `srv-xxxxxxxxxxxx` |
| `RENDER_WORKER_SERVICE_ID` | Worker service page URL: `srv-xxxxxxxxxxxx` |
| `PROD_ENV_FILE` | Full contents of a production `backend/.env` (see `backend/.env.example` for the field list) — written to disk and passed to the migration container via `--env-file` |
| `PROD_DOMAIN` | The domain Caddy/Render terminates TLS for, e.g. `app.example.com` — used for the post-deploy smoke test |

`PROD_ENV_FILE`'s `DATABASE_URL` must be the Postgres instance's **External**
connection string — the GitHub-hosted runner isn't on Render's private
network, only Render's own services are.

## What CI does on a push to `main`

1. `alembic upgrade head` runs inside the freshly-built, freshly-scanned
   image against the production DB. Failure aborts — nothing below runs.
2. Triggers a Render deploy for both services via the
   [Deploys API](https://api-docs.render.com/reference/create-deploy),
   pointing at the new image.
3. Polls the backend deploy until Render reports `live` (or a failed/canceled
   status, which fails the job).
4. Smoke-tests `https://$PROD_DOMAIN/health/ready`.
5. On smoke-test failure, calls the
   [rollback endpoint](https://api-docs.render.com/reference/rollback-deploy)
   with the deploy ID that was live before this run started.

Verify the exact JSON shape of the `GET /deploys` list response (wrapped vs.
flat items) against Render's live API or dashboard before relying on step 5 of
that list in anger — it was implemented defensively (`.deploy // .`) against
documentation that didn't show an example response at the time this was written.

## Verification

The `deploy` job's own smoke test (step 4 of "What CI does" above) is the
automated half — it checks `https://$PROD_DOMAIN/health/ready` and rolls back
if it fails. Do the other half by hand the first time, because a green
`/health/ready` only proves the process reached Postgres and Redis:

- Register or log in through the real domain, and confirm the refresh cookie
  comes back as `__Host-refresh_token` with `Secure`/`HttpOnly` — a
  reverse-proxy or domain misconfiguration shows up here and nowhere else.
- Confirm the worker is actually consuming: trigger something that enqueues a
  job (a password-reset email) and watch it arrive, rather than trusting the
  worker service's "live" badge.
- `curl -i https://$PROD_DOMAIN/metrics` from outside → not 200. Caddy blocks
  it externally, and this is the cheapest check that the right Caddy config is
  in front.
- The negative check: hit the backend service's own Render URL directly. If it
  answers, it is reachable without going through your edge, and the TLS/header
  guarantees above are bypassable.

## Rollback

CI rolls itself back: its step 5 calls Render's rollback endpoint with the
deploy ID that was live before the run, whenever the smoke test fails. Verify
that path against Render's live API before relying on it in anger — the
caveat under that list explains why it was written defensively.

Manually: Render dashboard → the service → **Deploys** → *Rollback* on the
last known-good deploy. Both services need it, and they are independent — a
rolled-back backend against a still-new worker is a real state to end up in.

**The migration is the part that does not roll back.** Its step 1 runs
`alembic upgrade head` against the production database *before* either
service is redeployed, so a rollback restores the old image against the new
schema. That is fine for additive migrations and is not fine for a destructive
one; a migration that drops or renames anything needs the expand/contract
split across two deploys, not a rollback plan. Data-level recovery is
`backup-restore.md`.

## Escalation

- **The `deploy` job fails on its first step**: the secrets are missing or
  misnamed. That is the dormant state described at the top, not an incident.
- **Migration step fails**: nothing has deployed yet — the old image is still
  serving. Fix forward on the migration; do not re-run the job hoping for a
  different result against a half-applied schema. Check `alembic current`
  against production before the second attempt.
- **Deploy reports live but the smoke test fails**: usually DNS/TLS
  (`PROD_DOMAIN` wrong, certificate not yet issued) rather than the app.
  Confirm by hitting the Render-provided URL directly before rolling back.
- **Rollback itself fails** (the API shape changed, or no previous deploy
  exists): roll back from the dashboard by hand, and fix the job afterwards —
  do not leave a broken deploy live while debugging the automation.

## The second host

T-307 puts marketing on its own hostname rather than under a path on the app's.
Two DNS records, two certificates, one edge:

| Host | Serves | Where it is configured |
|---|---|---|
| `example.com` (apex) | `apps/marketing/dist`, statically | `MARKETING_DOMAIN` in `backend/.env` |
| `app.example.com` | the API and the SPA | `DOMAIN` in `backend/.env` |

**DNS.** An `A`/`AAAA` record for the apex and one for `app.`, both pointing at
the host running the stack. Caddy obtains a certificate per hostname over
HTTP-01, so both records must resolve *before* the stack comes up or the first
certificate order fails (it retries; the site is unavailable meanwhile). A CDN
in front of either host must forward the `Host` header unchanged, or Caddy
serves the wrong site block.

**`MARKETING_DOMAIN` must never be blank.** Caddy substitutes a set-but-empty
environment variable as the empty string rather than falling back to the
default in `{$MARKETING_DOMAIN:...}`, which leaves a site block with no address
— and Caddy then reads its opening brace as a global-options block and refuses
to start the **whole edge, app host included**. `backend/.env.example` ships
`marketing.localhost`, a name nobody resolves, precisely so that forgetting
this setting costs you the marketing host and not the product.

**The block Caddy serves is generated by the build.** `pnpm build` writes
`apps/marketing/caddy/prod/marketing.caddy`, which `docker-compose.prod.yml`
bind-mounts to `/etc/caddy/conf.d`, and `Caddyfile.prod` imports as a glob. So:

- Deploying marketing means running `pnpm build` on the host *before*
  `docker compose -f docker-compose.prod.yml up -d`, exactly as `apps/web/dist`
  already requires. A stale `dist/` with a fresh block, or the reverse, is the
  one failure mode — its `Content-Security-Policy` names each inline script by
  hash, so a mismatch means a site whose JavaScript is silently blocked.
  Rebuild both together; `caddy/*/*.caddy` is a turbo output, so a cached build
  restores them in step.
- With `modules.marketing: false` there is no `dist/` and no block, the glob
  matches nothing, and the app host is untouched. That is the intended state
  for a product with no marketing site, not a misconfiguration.

**Two more things have to be set before the site is indexable**, and both fail
silently rather than loudly:

- `PRODUCTION_ORIGIN` in `apps/marketing/src/config/indexing.ts` ships as the
  placeholder `https://example.com`. Change it to the real apex. It is also
  what the production site block's `X-Robots-Tag` rule is written against, so
  every *other* host — staging, a preview deploy — is told `noindex` without
  anybody having to remember.
- The environment that builds the site must set `PUBLIC_SITE_URL` to exactly
  that origin. Any other value, including leaving it unset, is treated as a
  staging build: `robots.txt` disallows every crawler and offers no sitemap.
  That is deliberate — a staging copy outranking production is expensive to
  undo — and every build prints which way it decided, so the build log is
  where to check.

**The session cookie stays on the app host.** It is named
`__Host-refresh_token`, a prefix browsers reject outright if a `Domain`
attribute is present, so it cannot be widened to `.example.com` to make a
"signed in" state visible on marketing — and it should not be. Marketing is
static, cacheable and needs no session; if it ever needs to know, that is a
public endpoint. The note is repeated in both Caddyfiles, and
`backend/tests/test_caddy_config.py` fails on any `set_cookie(domain=...)`.

**Checking a deploy by hand**, without waiting for DNS to propagate to you:

```sh
curl -sI --resolve example.com:443:<server-ip> https://example.com/en/
```

Expect `200`, a `Content-Security-Policy` whose `script-src` is `'self'` plus
hashes, `Cache-Control: public, max-age=300`, and **no** `X-Robots-Tag` (its
presence on the production host means `PRODUCTION_ORIGIN` is still the
placeholder). `/_astro/<hash>.css` should answer with
`Cache-Control: public, max-age=31536000, immutable`.

## Not yet wired up

- Playwright E2E against staging (`playwright test --project=staging` in the
  plan) — lands with the Playwright suite in T-220 (Phase 3).
- Branch protection on `main` (green CI + 1 review, linear history, no direct
  push) — a GitHub repo setting, not code; configure manually via
  Settings → Branches, or `gh api repos/<owner>/<repo>/branches/main/protection`,
  once this repo has a remote.
- Marketing on Render specifically. T-307 serves `apps/marketing` from the
  self-hosted Caddy stack (see "The second host" below), which is the
  `docker-compose.prod.yml` path. On Render the equivalent is a **Static Site**
  service pointed at `apps/marketing/dist` with the same headers configured in
  its dashboard — Render's edge is not Caddy and does not read the generated
  site block. Nobody has done that here; the header list to copy is in
  `apps/marketing/caddy/prod/marketing.caddy` after a build.
