# Deploy pipeline runbook

How a commit on `main` becomes a running deployment, and how to point that at
Render, a Hetzner VPS, or AWS ECS. The platform-specific halves live in
`deploy-render.md` and in the header comments of each workflow; this file
covers the part they share and how to choose between them.

## Trigger

Reach for this when:

- **standing up a new environment**, or moving one between platforms
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
  Unset means `render`, which is what CI did before the split. `none` stops
  CI deploying at all, which is the right setting while a host is still
  deployed by hand.
- **`PROD_ENV_FILE` is the whole `backend/.env`**, as one secret. Every
  target reads it. `Settings()` validates every required field at import, so
  a partial file fails the migration step rather than the app's first
  request.
- **The runner never reaches the production database** (review C4,
  2026-09-29). Each target migrates on its own infrastructure; see step 3.
- **Deploys wait for the whole gate**: `build` (and through it `test` and
  `security`), `e2e-web` and `zap-baseline`. A red browser suite blocks a
  deploy (C2).
- **Actions minutes.** Image builds are the most minute-hungry thing in this
  repo. This pipeline was written while the account's quota was exhausted, so
  none of it has run end to end — see Escalation.
- **For Hetzner only:** `SSH_HOST`, `SSH_USER`, `SSH_KEY`, `SSH_KNOWN_HOSTS`
  (from `ssh-keyscan <host>`, pinned rather than trusted on first use) and
  `PROD_DOMAIN`.
- **For AWS only:** the infrastructure must already exist. There is no
  Terraform or CDK here — the workflow deploys to an ECS service, it does not
  create one.

## Steps

### 1. Choose the target

```bash
gh variable set DEPLOY_TARGET --body hetzner   # or render, aws, none
```

**Expected:** `gh variable list` shows it. Only the matching job runs; the
other two are skipped by their `if:`, visible as skipped jobs in the run.

### 2. Let `main` build

Every push to `main` builds the backend image, scans it with Trivy (high+,
fixable only), and pushes to `ghcr.io/<owner>/<repo>:<sha>` — **only if the
scan passes**, so a vulnerable image never reaches the registry a deploy
pulls from. On `main` it also builds `…-caddy:<sha>`, the edge image with the
frontend baked in.

**Expected:** both tags visible under the repository's Packages.

### 3. Watch the preflight and the migration

Whichever target runs, `.github/actions/deploy-preflight` does the same three
things first, on the runner, before anything is touched:

1. logs in to GHCR
2. writes `prod.env` from `PROD_ENV_FILE`, failing if it is empty. The
   secret reaches the script through `env:`, never `${{ }}` pasted into its
   source, so a `'` or `$(` in a password is data rather than shell (C4;
   `test_preflight_writes_a_hostile_env_file_verbatim` runs the real step)
3. refuses to continue if it contains `VIRUS_SCANNER=noop`

Then each target **migrates on its own infrastructure, from the image that
is about to serve traffic**, before it switches:

| Target | Where the migration runs | Where its output is |
|---|---|---|
| Hetzner | `docker compose run --rm --no-deps backend …` over SSH, step *Migrate on the box* | the GitHub run |
| AWS | a one-off Fargate task in the backend service's network, step *Run migrations as a one-off task* | the backend's log group; the run shows the exit code |
| Render | the backend service's Pre-Deploy Command, which the workflow checks is set | the deploy's logs on the Render dashboard |

**Expected:** the migration ends without error. This is the step to read
first when a deploy fails — it tells you whether the schema moved.

It is always `python -m app.core.migrations`, never `alembic upgrade head`.
This schema has branched module heads; `head` (singular) could never have
succeeded against it.

### 4. Deploy, then verify from outside

Each target then swaps traffic its own way and finishes with
`curl -sf https://$PROD_DOMAIN/health/ready`.

**Expected:** a green run. On Hetzner the swap is
`docker compose up -d --no-deps backend worker scanner-worker caddy` — it is
idempotent, since the images were pulled and the env swapped during the
migration step, so a re-run converges instead of failing. **`--no-deps` is
load-bearing**: without it compose stops caddy and then waits for its
dependency chain, and the backend refuses to start while caddy is down
because caddy carries the network alias for `STORAGE_DOMAIN` and the storage
guard correctly refuses to reach object storage over the public internet.
That deadlock took production down on 2026-09-14.

### 5. Redeploy or roll forward by hand

Every deploy workflow also accepts `workflow_dispatch` with an image tag, so
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

And confirm the running code is the code you think it is. On Hetzner:

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

## Rollback

**Automatic.** Each workflow records what was running before it changed
anything and restores it on failure: Render rolls back to the captured
deploy id when the smoke test fails; Hetzner restores the previous
`backend/.env` and checkout and re-runs `up -d` with the previous tag; AWS
re-points the service at the previous task definition and waits for
stability.

On Hetzner and AWS the rollback runs whenever the switch *started*,
including one that failed halfway (C5) — some containers on the new image,
some on the old is exactly the state that most needs undoing. On Hetzner it
also runs after a failed migration, because by then the env file and
checkout on the box have already moved.

**Manual**, for anything the smoke test did not catch:

```bash
gh workflow run deploy-hetzner.yml -f image-tag=<previous sha>
```

**The database does not roll back.** Only images and task definitions move
backwards; a migration that has applied stays applied. That is the point of
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
  and CI cannot fix it. Go to the host: on Hetzner,
  `docker compose … up -d --no-deps caddy` first, then the backend — caddy
  must be up for the backend's storage guard to pass.
- **A deploy target you did not configure ran.** `DEPLOY_TARGET` is unset and
  therefore defaulting to `render`. Set it explicitly, including to `none`.

**None of these workflows has run end to end.** They were written on
2026-09-14 while the account's Actions minutes were exhausted; the pieces
that could be validated locally were (compose overlays resolved with
`docker compose config`, the edge image built and its contents inspected,
all four workflows linted with actionlint), and the pieces that need a real
runner were not. Treat the first live run as a test: use `workflow_dispatch`
against a non-production target, or watch it with a rollback ready.
