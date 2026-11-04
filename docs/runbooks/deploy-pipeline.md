# Deploy pipeline runbook

How a commit on `main` becomes a running deployment on a Hetzner server. Setting
up the server the first time is `new-server.md`.

This is the only deploy path in the template. Render and AWS workflows existed
until 2026-11-03 but had never run, so they were removed; git history has them.

## Trigger

Reach for this when:

- **standing up a new environment**
- **a deploy failed** and you need to know what had already happened when it
  did — in particular whether the database was migrated before it stopped
- **you need to redeploy without a commit** — a rollback, or re-running a
  deploy that failed on something transient
- **CI is deploying somewhere you did not expect**, or not deploying at all

Not this runbook: application faults after a successful deploy (that is
`degraded-mode.md`), or billing/webhook drift (`billing-drift.md`).

## Pre-checks

- **Know which target is live.** `DEPLOY_TARGET` is a repository *variable*
  (Settings → Secrets and variables → Actions → Variables), not a secret.
  Unset deploys nothing; set `hetzner` to deploy from CI.
- **The whole `backend/.env` comes from one place:** the Bitwarden Secrets
  Manager project `forge-prod`, one secret per line, read with
  `BWS_ACCESS_TOKEN`. `Settings()` validates every required field at import, so
  a partial file fails the migration step rather than the app's first request.
- **The runner never reaches the production database** (review C4,
  2026-09-29). The migration runs on the box; see step 3.
- **Deploys wait for the whole gate**: `build` (and through it `test` and
  `security`), `e2e-web` and `zap-baseline`. A red browser suite blocks a
  deploy (C2).
- **Actions minutes.** Image builds are the most minute-hungry thing in this
  repo.
- **Repository secrets:** `SSH_HOST`, `SSH_USER`, `SSH_KEY`, `SSH_KNOWN_HOSTS`
  (from `ssh-keyscan <host>`, pinned rather than trusted on first use) and
  `PROD_DOMAIN`.

## Steps

### 1. Choose the target

```bash
gh variable set DEPLOY_TARGET --body hetzner
```

**Expected:** `gh variable list` shows it. Anything else, or unset, skips the
deploy job, visible as a skipped job in the run.

### 2. Let `main` build

Every push to `main` builds the backend image, scans it with Trivy (high+,
fixable only), and pushes to `ghcr.io/<owner>/<repo>:<sha>` — **only if the
scan passes**, so a vulnerable image never reaches the registry a deploy
pulls from. On `main` it also builds `…-caddy:<sha>`, the edge image with the
frontend baked in.

**Expected:** both tags visible under the repository's Packages.

### 3. Watch the preflight and the migration

`.github/actions/deploy-preflight` does three things first, on the runner,
before anything is touched:

1. logs in to GHCR
2. checks `prod.env`, already written from Bitwarden, failing if it is empty.
   A secret reaches the script through `env:`, never `${{ }}` pasted into its
   source, so a `'` or `$(` in a password is data rather than shell (C4;
   `test_preflight_writes_a_hostile_env_file_verbatim` runs the real step)
3. refuses to continue if it contains `VIRUS_SCANNER=noop`

Then it **migrates on the box, from the image that is about to serve traffic**,
before it switches: `docker compose run --rm --no-deps backend …` over SSH, step
*Migrate on the box*. Its output is in the GitHub run.

**Expected:** the migration ends without error. This is the step to read
first when a deploy fails — it tells you whether the schema moved.

The same step first brings the data services (Postgres, pgbouncer,
Redis, MinIO, ClamAV and the exporters) to what the checkout says, and waits for
them to be healthy. A release that does not change them leaves them running. One
that does, such as a Dependabot digest bump, restarts them here, so the app is
briefly down from this point until the switch below. Changes to the `.env.*` files
on the box take effect the same way, at the next deploy.

It is always `python -m app.core.migrations`, never `alembic upgrade head`.
This schema has branched module heads; `head` (singular) could never have
succeeded against it.

### 4. Deploy, then verify from outside

It then swaps traffic and finishes with
`curl -sf https://$PROD_DOMAIN/health/ready`.

**Expected:** a green run. The swap is
`docker compose up -d --no-deps backend worker scanner-worker caddy` — it is
idempotent, since the images were pulled and the env swapped during the
migration step, so a re-run converges instead of failing. **`--no-deps` is
load-bearing**: without it compose stops caddy and then waits for its
dependency chain, and the backend refuses to start while caddy is down
because caddy carries the network alias for `STORAGE_DOMAIN` and the storage
guard correctly refuses to reach object storage over the public internet.
That deadlock took production down on 2026-09-14.

Behind Cloudflare, the last step connects to the origin directly and goes
red if 80 or 443 answers. **The release has stayed live and needs no rollback.**
Fix the firewall (`new-server.md`, step 1) and re-run the job.

### 5. Redeploy or roll forward by hand

The deploy workflow also accepts `workflow_dispatch` with an image tag, so
you can deploy any already-built SHA without pushing a commit:

```bash
gh workflow run deploy-hetzner.yml -f image-tag=<sha>
```

**Expected:** the run appears immediately and goes through the same preflight
as an automatic deploy — including the migration.

## Verification

From outside the pipeline, not from its own logs:

```bash
curl -sf https://<domain>/health/ready
```

And confirm the running code is the code you think it is:

```bash
ssh <box> "cd ~/forge && docker compose \
  -f docker-compose.prod.yml -f docker-compose.tls.yml -f docker-compose.registry.yml \
  ps --format '{{.Service}} {{.Image}}'"
```

**Expected:** backend, worker and caddy all on the tag you deployed.

The negative check — the thing that still looks fine if the deploy silently
did nothing: compare the served bundle's hash before and after. A green
health check proves the old container is healthy just as well as a new one.

```bash
curl -s https://<domain>/ | grep -o 'assets/index-[^"]*\.js'
```

Do the rest by hand the first time, because a green `/health/ready` only proves
the process reached Postgres and Redis:

- Register or log in through the real domain, and confirm the refresh cookie
  comes back as `__Host-refresh_token` with `Secure`/`HttpOnly`. A reverse-proxy
  or domain misconfiguration shows up here and nowhere else.
- Confirm the worker is actually consuming: trigger something that enqueues a
  job (a password-reset email) and watch it arrive, rather than trusting its
  health badge.
- `curl -i https://<domain>/metrics` from outside: not 200.
- **The app origin's document headers.** `curl -sI https://<domain>/` must show
  `content-security-policy`, `x-frame-options`, `x-content-type-options`,
  `referrer-policy` and `permissions-policy`. These come from Caddy, not from
  `app.middleware.security_headers`: that middleware protects API responses and
  never sees the SPA's HTML.
- **Then open the app with the browser console visible and use it.** The CSP is
  enforced, not report-only, and a blocked upload is silent apart from the
  console. `img-src` and `connect-src` name the storage host, the Google and
  GitHub picture hosts and Sentry, so a storage host that is not
  `STORAGE_DOMAIN` needs adding in `Caddyfile.prod`. `script-src` is `'self'`
  plus one hash, which covers the inline
  theme-boot script in `apps/web/index.html`. Editing that script changes its
  hash and blocks it; the symptom is a white flash into a dark app, and
  `backend/tests/test_caddy_config.py` fails the build before it can reach you.

## Rollback

**Automatic.** The workflow records what was running before it changed
anything and restores it on failure: the previous `backend/.env` and checkout,
then `up -d` with the previous tag.

The rollback runs whenever the switch *started*, including one that failed
halfway (C5) — some containers on the new image, some on the old is exactly the
state that most needs undoing. It also runs after a failed migration, because by
then the env file and checkout on the box have already moved.

It then waits for the previous release to be healthy. A red run whose
log says `ROLLBACK FAILED` means the site is down: see Escalation.

**Manual**, for anything the smoke test did not catch:

```bash
gh workflow run deploy-hetzner.yml -f image-tag=<previous sha>
```

**The database does not roll back.** Only images move backwards; a migration that has applied stays applied. That is the point of
no return in every deploy here, and it is why migrations must work against
the previous release as well as the new one. If a migration is not
backwards-compatible, it needs to ship in two releases — add the new shape,
deploy, then remove the old — not be undone afterwards.

## Escalation

Stop and escalate when:

- **The migration failed.** Nothing was deployed, which is the
  design, but the schema may be partially migrated depending on where it
  stopped. Read the alembic output before re-running; a second attempt
  against a half-applied revision is how a bad situation gets worse.
- **The smoke test failed and the rollback also failed.** The site is down
  and CI cannot fix it. Go to the host:
  `docker compose … up -d --no-deps caddy` first, then the backend — caddy
  must be up for the backend's storage guard to pass.
- **A deploy ran that you did not expect.** Check the repository variable:
  only an explicit `DEPLOY_TARGET` deploys.

## Failure drill

**2026-11-02, on forge-prod-2 (C6).** Each case deployed an older
release by hand with one failure injected, measured from outside.

| Injected | What happened | Site down |
|---|---|---|
| A revision alembic cannot find, added to `alembic_version` | Migration failed after the backup. Switch never ran, containers kept their start times | Never |
| `PROD_DOMAIN` set to a host that does not exist | Release came up healthy, smoke test failed after 2 min, rolled back | Not measured: public DNS still pointed at another box |
| `docker compose up` killed with SIGKILL 1 s into the switch | Half-created containers removed, rolled back | About 19 s |

The drill found that the smoke test had been passing against a different
server, and that the rollback did not check its own result. Both are now
covered by `scripts/wait-for-release.sh`.

## Object-store lifecycle rules (S3 or R2 only)

On MinIO, `minio-init` installs both rules. On S3 or R2, add them to the
bucket once, with the provider's lifecycle settings or
`aws s3api put-bucket-lifecycle-configuration` (the JSON `minio-init` imports
is in `docker-compose.prod.yml`):

- **Expire prefix `incoming/` after 1 day.** Uploads land there and are
  copied out on confirm (review R1); a re-posted form can leave a copy no row
  points at. Without the rule they just accumulate outside anyone's quota.
- **Expire prefix `data-exports/` after 1 day.** GDPR export archives; the
  download link lasts 24 hours (review OCT-03). Without the rule every
  export is kept indefinitely -- a copy of that user's data the purge only
  catches if the account is later erased.

## Restarting one service on the compose host

`docker compose` composes the **set of files you pass it**, and nothing warns
you when that set is smaller than the one a container was created with. The
missing overlays' contributions — bind mounts especially — are simply absent
from the recreated container.

This happened on 2026-09-25 and took the API down for about eight minutes.
`docker compose -f docker-compose.prod.yml up -d --force-recreate backend worker`
looks complete and is not: this host's stack was created with the TLS and
host-build overlays too. Dropping the TLS one dropped `./certs:/certs:ro`, so
`rediss://redis:6379/0?ssl_ca_certs=/certs/ca.crt` could not open its CA and
every request touching Redis answered 500.

**The failure is quiet in the worst way.** `docker ps` reported the backend
*healthy* throughout, because its healthcheck hits `/health`, which touches
nothing. `/health/ready` was the only endpoint telling the truth, and the SPA
and marketing site stayed up because Caddy serves those off disk — so the
site looked fine while every API call failed.

Recover the real set from a container you have **not** recreated, rather than
from memory or from this document:

```bash
docker inspect forge-caddy-1   --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
```

Then pass all of them:

```bash
docker compose -f docker-compose.prod.yml   -f docker-compose.tls.yml -f docker-compose.host-build.yml   up -d --force-recreate backend worker
```

Afterwards, check the labels match what you started from, and check
`/health/ready` rather than the container's health badge:

```bash
docker inspect forge-backend-1   --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
curl -s -o /dev/null -w '%{http_code}
' https://$PROD_DOMAIN/health/ready
```

## The second host

T-307 puts marketing on its own hostname rather than under a path on the app's.
Two DNS records, two certificates, one edge:

| Host | Serves | Where it is configured |
|---|---|---|
| `example.com` (apex) | `apps/marketing/dist`, statically | `MARKETING_DOMAIN` in `backend/.env` **and** the compose `.env` |
| `app.example.com` | the API and the SPA | `DOMAIN` in `backend/.env` **and** the compose `.env` |

**Both names have to be in two places, and that is deliberate.** The edge
container no longer takes `env_file: ./backend/.env`. That file holds
`JWT_PRIVATE_KEY`, `ENCRYPTION_KEY`, `DATABASE_URL` and `STRIPE_SECRET_KEY`,
and handing all of it to the one container the internet can reach meant an
edge compromise yielded token forgery, database access and decryption — none
of which Caddy needs in order to terminate TLS and proxy. It now gets an
explicit allowlist of four non-secret values: `DOMAIN`, `MARKETING_DOMAIN`,
`STORAGE_DOMAIN`, `CADDY_TRUSTED_PROXIES`.

Compose interpolates `${...}` only from the shell and from the compose
project's own `.env` (see `.env.example` at the repo root) — never from
`backend/.env`, which it treats purely as a container env file. So those four
must be in the root `.env` as well. `DOMAIN` has no default and compose
refuses to start without it, which is the intended failure: an edge that
booted on a fallback hostname would serve the wrong certificate.

To confirm the edge is clean:

```
docker compose -f docker-compose.prod.yml exec caddy env | grep -E 'JWT|ENCRYPTION|DATABASE|STRIPE'
```

Expect no output. `backend/tests/test_caddy_config.py` fails the build if the
allowlist grows a name the Caddyfiles do not use, or loses one they do.

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

- `Dockerfile.caddy` builds the bundle and its block together, so a CI deploy
  cannot mismatch them. Its `Content-Security-Policy` names each inline script
  by hash, so if you ever build by hand, rebuild both: a stale `dist/` with a
  fresh block means a site whose JavaScript is silently blocked.
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
- **`PUBLIC_APP_URL` must be set to the app's public origin**, or every link
  into the app falls back to `https://localhost` — the header's Sign in, the
  hero's Register and Log in, and the pricing CTA, i.e. every conversion path
  on the site. It shipped that way on this template's first production deploy
  and was found by a human clicking Sign in. `check-app-links.mjs` now fails
  any production build whose markup links to localhost, so this cannot recur
  silently.
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
