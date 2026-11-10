# Staging runbook

How to set up and run the staging server: a second box, with its own accounts,
that every merge deploys to before production. Production gets the same backend
image only after staging runs it healthy (`deploy-pipeline.md`). The server half
is `new-server.md`; this file is what differs for staging.

Agreed 2026-11-07. Staging goes away with forge-prod-2 when product work starts,
and a product sets up its own from this runbook.

## Trigger

- A product, or this template, needs the staging gate in front of production.
- Production deploys are waiting because `deploy-hetzner-staging` fails with
  "The staging environment has no SSH_HOST": staging is not set up yet.
- The staging box is replaced.

Not this runbook: a deploy that failed on a working staging (`deploy-pipeline.md`),
or production's own server (`new-server.md`).

## Pre-checks

- **Nothing is shared with production.** Its own Bitwarden project and machine
  account, its own JWT, encryption and URL-token keys, its own Stripe sandbox,
  Google OAuth client, Resend API key and backup bucket. A staging secret that
  leaks then says nothing about production, and a scan cannot touch production
  data.
- **Hosts one level deep:** Cloudflare's free certificate covers `*.<domain>` and
  nothing deeper, so `app-staging.<domain>`, not `app.staging.<domain>`.
- **A box like production's:** x86, 4 GB, an EU location, the same firewall.

## Steps

### 1. Create the accounts

Make each of these with a fresh value. None is a copy of production's.

| Where | Name | Notes |
|---|---|---|
| Hetzner | server `forge-staging` | as `new-server.md` step 1, with the same firewall |
| Cloudflare | `staging.`, `app-staging.`, `storage-staging.` A records | Proxied, to the new server |
| Bitwarden Secrets Manager | project `forge-staging` | every secret `forge-prod` has, with staging values; its ID in `deploy/staging.env` |
| Bitwarden Secrets Manager | machine account `forge-staging-deploy` | **Can read** on `forge-staging` only |
| Stripe | a sandbox, `forge-staging` | its own test keys, the same plans (`sync_plans`), a webhook to `app-staging.` |
| Google Cloud | OAuth client `forge-staging` | redirect `https://app-staging.<domain>/api/v1/auth/oauth/google/callback` |
| Resend | API key `forge-staging` | sending access, the domain production already verified |
| Cloudflare R2 | bucket `forge-staging-backups` | lock and expiry as `backup-restore.md`, a week of each is enough |
| age | `forge-staging-backup-key.txt` | made on your machine, stored in the Password Manager |

Staging's settings are committed, in `deploy/staging.env`; `deploy/common.env` has
what it shares with production. What differs:

- `ENVIRONMENT=staging`. The deploy refuses a file whose `ENVIRONMENT` is not the
  environment it deploys to.
- `DOMAIN=app-staging.<domain>`, `MARKETING_DOMAIN=staging.<domain>`,
  `STORAGE_DOMAIN=storage-staging.<domain>`, and every URL built from them. The
  edge image's bundles take their URLs from these two domains.
- `API_RATE_LIMIT` and `ORG_RATE_LIMIT` as in `backend/.env.loadtest`, so the weekly
  scan is not answered with 429s. `AUTH_RATE_LIMIT` stays at the default: the box is
  public, and its sign-in routes are as reachable as production's.

**Expected:** each row exists, and the Bitwarden project holds only staging secrets.

### 2. Bootstrap the box

As `new-server.md` steps 3 and 4, with `staging` as the third argument:

```bash
scp scripts/bootstrap-hetzner-box.sh root@<ip>:
ssh root@<ip> bash bootstrap-hetzner-box.sh git@github.com:<owner>/<repo>.git age1... staging
```

`staging` names the backup's Sentry monitor `forge-backup-staging`, so the two
boxes cannot check in for each other, and keeps a week of local copies.

**Expected:** the script ends with `Done.`, and `~forge/forge-backup.env` has
`BACKUP_SENTRY_MONITOR=forge-backup-staging`. Point the backups at
`forge-staging-backups` with `set-backup-remote.sh`. Step 4's script takes
`staging` too: `bash scripts/fill-bitwarden.sh staging root@<ip>`.

### 3. Lock CI's key and connect GitHub

As `new-server.md` step 5, with a new key, `~/.ssh/forge_staging_ci_deploy`, and a
GitHub environment named **`staging`**, deployment branches limited to `main`:

| Kind | Name | Value |
|---|---|---|
| secret | `BWS_ACCESS_TOKEN` | `forge-staging-deploy`'s token |
| secret | `SSH_KEY`, `SSH_KNOWN_HOSTS`, `SSH_HOST`, `SSH_USER` | the new key, the box's host key, its IP, `forge` |

No variables: the bundles' URLs come from `deploy/staging.env`, and
`VITE_SENTRY_DSN` stays the repository's, since Sentry separates the two by
`ENVIRONMENT`.

**Expected:** `gh secret list --env staging` lists the five secrets.

### 4. First deploy

Re-run the latest `main` CI run's failed `deploy-hetzner-staging` job, or:

```bash
gh workflow run deploy-hetzner.yml -f image-tag=<sha on main> -f environment=staging
```

**Expected:** `deploy-hetzner-staging` green. A re-run from the CI run goes on to
production.

### 5. Seed it, and close sign-ups

On the box, with your own key:

```bash
ssh forge@<ip> 'docker exec -i "$(docker ps -qf label=com.docker.compose.service=backend)" python -m app.core.seed_pentest'
```

It prints each account's password once. Keep them in the Password Manager. Then
sign in at `https://app-staging.<domain>` as `pentest-superadmin@example.com`, and
under **Admin → Product settings** turn sign-ups off. Staging is public, and nobody
outside should make an account there.

**Expected:** a new sign-up at `app-staging.` is refused.

### 6. Turn on the weekly scans

Repository variables `STAGING_URL=https://app-staging.<domain>` and
`ZAP_SCANNER_ORG_ID` (the "Pentest Engagement" org's ID), and repository secrets
`ZAP_SCANNER_EMAIL=pentest-owner@example.com` and `ZAP_SCANNER_PASSWORD`. ZAP's full
scan, the TLS check and k6 run on Sunday nights from then on, and **Run workflow**
runs them any time.

**Expected:** a manual run of `ZAP Full Scan` finishes and uploads its reports.

## Verification

- `https://app-staging.<domain>/health/ready` answers 200.
- `https://staging.<domain>/robots.txt` disallows everything. The marketing build
  does that for any origin but production's.
- The next merge to `main` shows `deploy-hetzner-staging` green before
  `deploy-hetzner` starts.
- CI's key opens no shell: `ssh -i ~/.ssh/forge_staging_ci_deploy forge@<ip>` prints
  the script's usage and closes.
- After the first night, Sentry shows the `forge-backup-staging` monitor.

## Rollback

A failed staging deploy rolls itself back, like production's, and production does
not start. To take staging out of the chain, remove the `deploy-hetzner-staging`
job and its `needs` entry from `.github/workflows/ci.yml`. Otherwise production
waits for it forever. To remove staging, delete the server, its DNS records, the
`staging` environment, the Bitwarden project, the Stripe sandbox, the Google
client, the Resend key and the R2 bucket once its lock allows, and then the Sentry
monitor, before the next 03:30 UTC (`backup.sh` re-creates a monitor at each check-in).

## Escalation

- **Staging is red and production is waiting.** Fix staging. For a production
  outage, deploy production by hand (`deploy-pipeline.md`, step 5).
- **The scan reports what Cloudflare did, not the app.** It goes through
  Cloudflare's WAF and rate limits. To scan the app itself, run it against a local
  copy of the stack, as on 2026-11-03.
- **Staging's pages point at production, or `robots.txt` allows crawling.** The
  commit's edge image was built before the `staging` variables existed, and a tag
  is built once. Deploy a newer commit.
- **`The box did not run scripts/deploy-box.sh`.** The key is not locked, or the
  checkout predates the script. Run `lock-deploy-key.sh` again.
- **A rollback refused with "older than scripts/deploy-box.sh".** Only the first
  deploy after a box is locked can meet this: the release it would go back to
  predates the locked key. Staging ran the same release first, so it should be
  rare. Roll forward with a fix, or put the old release back by hand with your own
  key.
