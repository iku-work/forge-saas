# New server runbook

How to take a fresh Hetzner server from nothing to a product deployed on it by
CI. `scripts/bootstrap-hetzner-box.sh` does the server half;
`.github/workflows/deploy-hetzner.yml` does every deploy after that. The deploy
side is in `deploy-pipeline.md`. This file covers what happens once, before the
first deploy.

Written from the first deploy to a fresh box (forge-prod-2, 2026-10-26). Each
"Why" below is something that failed that day.

## Trigger

Reach for this when:

- **a new product** built from this template needs its production (or
  staging) server;
- **a server is replaced** (new region, new size, a rebuilt box). Run this on
  the new one, move the data (`backup-restore.md`), then switch DNS.

## Pre-checks

- **Accounts:** Hetzner, Cloudflare (the domain), Bitwarden Secrets Manager
  (EU), and GitHub admin on the product repo.
- **A Bitwarden project for this server**, holding the app's settings: one
  secret per `backend/.env.example` name, with your own values. Never copy a
  value from a committed `.env.*` file. Production refuses the keys that were
  once committed (`_PUBLISHED_KEY_FINGERPRINTS` in `app/core/config.py`), and
  the others are dummies.
- **A machine account** with **Can read** on that project, and an access token
  for it. The token is shown once; it goes straight into GitHub in step 5.

## Steps

### 1. Create the server

Hetzner Console → **Add Server**:

- **Location:** an EU one.
- **Image:** Ubuntu LTS.
- **Type:** x86, at least 4 GB RAM. Not Arm: CI builds amd64 images only.
  ClamAV alone needs about 1.5 GB.
- **SSH key:** your own.

**Firewalls → Create Firewall**, then attach it:

| Port | From | Why |
|---|---|---|
| TCP 22 | Any | CI deploys over SSH from GitHub's changing addresses; the script makes SSH key-only |
| TCP 80, 443 | Cloudflare's ranges ([cloudflare.com/ips](https://www.cloudflare.com/ips/)) | Nobody reaches the server around Cloudflare |

### 2. Point DNS at it, before the first deploy

In Cloudflare, add A records for the app host, the marketing host and the
storage host (`DOMAIN`, `MARKETING_DOMAIN`, `STORAGE_DOMAIN`), set to
**Proxied**. Set SSL/TLS to **Full (strict)**.

Why first: Caddy gets its certificates from Let's Encrypt by HTTP, and Let's
Encrypt can only reach the server once DNS points at it. On a server that DNS
does not point at yet, Caddy retries on a growing back-off, and HTTPS stays
down for as long as it waits. When replacing a server, restart caddy right
after switching DNS (`docker compose ... restart caddy`).

### 3. Run the bootstrap script

```bash
scp scripts/bootstrap-hetzner-box.sh root@<ip>:
ssh root@<ip> bash bootstrap-hetzner-box.sh git@github.com:<owner>/<repo>.git
```

The first run stops after printing a **read-only deploy key**. Add it on GitHub
under **Settings → Deploy keys → Add deploy key**, with write access left
unticked, and run the same command again. The second run:

- checks out the repo;
- generates the Postgres, MinIO and KMS secrets, and the internal TLS
  certificates;
- starts the stateful services.

Re-running it is safe: it never overwrites a generated password or
certificate.

### 4. Move the generated values into Bitwarden

The script writes `~forge/forge/bitwarden-values.txt` (mode 600) on the
server.

- Add each setting line to the Bitwarden project, then delete the file.
- Store `MINIO_KMS_SECRET_KEY` somewhere off the server **instead**, in the
  Password Manager and not in the deploy project. It decrypts every uploaded
  file.

Why: those values must match the passwords the services were just started
with, and the deploy reads the app's settings only from Bitwarden.

Pasting into Bitwarden: the value only, no surrounding quotes, and a PEM key
from `-----BEGIN` to `-----END ...-----` inclusive. The deploy fails, naming
the secret, on an unclosed quote or a PEM with no END line. It trims a trailing
newline.

### 5. Connect GitHub

On your machine, make a key for CI and authorise it on the server:

```bash
ssh-keygen -t ed25519 -N "" -C "github-actions deploy" -f ~/.ssh/<product>_ci_deploy
ssh root@<ip> "cat >> /home/forge/.ssh/authorized_keys" < ~/.ssh/<product>_ci_deploy.pub
ssh-keyscan -t ed25519 <ip> > ~/.ssh/<product>_known_hosts
```

GitHub → **Settings → Environments → New environment** `production`, with
deployment branches limited to `main`. Add these environment secrets.
`gh secret set NAME --env production < file` keeps the values off the
screen.

| Secret | Value |
|---|---|
| `BWS_ACCESS_TOKEN` | the machine account's token |
| `SSH_KEY` | `~/.ssh/<product>_ci_deploy` |
| `SSH_KNOWN_HOSTS` | `~/.ssh/<product>_known_hosts` |
| `SSH_HOST` | the server's IP |
| `SSH_USER` | `forge` |
| `PROD_DOMAIN` | the app host, used by the smoke test |

Then add these repository variables: `PUBLIC_SITE_URL`, `PUBLIC_APP_URL`,
`PUBLIC_API_URL` (the app host, https), and last, `DEPLOY_TARGET=hetzner`.

### 6. First deploy

Every push to `main` now deploys. For the first one, re-run the latest `main`
CI run (`gh run rerun <id>`). That run also builds the edge image, which is
only built once `DEPLOY_TARGET` is set.

## Verification

- The `deploy-hetzner / deploy` job is green. The workflow checks itself:
  - every service runs the release image and is healthy;
  - the smoke test passes `https://<PROD_DOMAIN>/health/ready`.
- From outside: `curl -sI https://<app host>/` returns 200, and the marketing
  host serves its landing page.
- On the server: `docker ps` shows backend, worker, scanner-worker and caddy
  next to the stateful services from step 3.
- Before anyone signs up: the Bitwarden `JWT_PRIVATE_KEY` and `ENCRYPTION_KEY`
  were generated for this product. A published key would already have stopped
  the deploy; a key shared with another product would not.

## Rollback

- **Before DNS points here:** nothing depends on the server. Delete it in
  Hetzner and start again.
- **After a failed deploy:** the workflow rolls back to the previous image by
  itself once one exists. A first deploy has nothing to roll back to. Fix the
  cause and push again.
- **Bootstrap problems:** the script is idempotent. Fix and re-run. To start
  the generated secrets over, stop the stack and remove its volumes first;
  new passwords do not open old data.

## Escalation

- **Deploy job errors that name a Bitwarden secret:** fix the secret, then
  re-run the job.
- **"refusing to deploy" / "can read no secrets":** the machine account has
  lost project access.
- **Caddy logs `acme ... unauthorized`:** DNS does not reach this server yet.
  See step 2.
- **The backend refuses to start with "resolves to the public address":** the
  storage alias needs caddy running. The deploy starts caddy first; on a
  hand-run `up`, do the same: `up -d --no-deps caddy` before the backend.
- Anything else: `deploy-pipeline.md`, then the workflow's header comments.
