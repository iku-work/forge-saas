# Deploying to Render

The `deploy` job in `.github/workflows/ci.yml` targets [Render](https://render.com)
and is dormant until the secrets below are configured on the repo
(Settings → Secrets and variables → Actions). Until then, pushes to `main`
build and push the image but the `deploy` job fails on its first step.

## 1. Services to create on Render

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

## 2. Registry credential (only if the GHCR package is private)

Dashboard → Account Settings → Registry Credentials → add a GitHub PAT with
`read:packages` scope, then attach it to both services.

## 3. GitHub Actions secrets to set

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

## 4. What CI does on a push to `main`

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
flat items) against Render's live API or dashboard before relying on step 5
in anger — it was implemented defensively (`.deploy // .`) against
documentation that didn't show an example response at the time this was written.

## 5. Not yet wired up

- Playwright E2E against staging (`playwright test --project=staging` in the
  plan) — lands with the Playwright suite in T-220 (Phase 3).
- Branch protection on `main` (green CI + 1 review, linear history, no direct
  push) — a GitHub repo setting, not code; configure manually via
  Settings → Branches, or `gh api repos/<owner>/<repo>/branches/main/protection`,
  once this repo has a remote.
