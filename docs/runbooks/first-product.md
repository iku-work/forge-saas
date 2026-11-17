# First product runbook

How to turn this template into a real product. `new-server.md` is the server half,
and this file is everything around it.

## Trigger

- You are starting a product from this template.
- A deploy failed with `refused:` in step *Refuse the template's identity*.

Not this runbook: pulling later template updates (`TEMPLATE.md`), or setting up a
server (`new-server.md`).

## Pre-checks

- **You have a name, a domain and a support address.** Most of what follows needs
  them.
- **You are making a new repository, not a GitHub fork.** A public fork cannot be
  made private, and it shares its network with the template.

## Steps

### 1. Make the repository yours

Create the new repository, and keep the template as a remote for updates, as
`TEMPLATE.md` describes.

**Expected:** `git remote -v` shows your repository as `origin` and the template as
`upstream`.

### 2. Give it your identity

| Where | What |
|---|---|
| `product.config.ts` | `name`, `primaryColor`, `logoUrl`, `supportEmail` |
| `backend/product/config.py` | the same values, which must match |
| `apps/marketing/src/config/indexing.ts` | `PRODUCTION_ORIGIN`, your site's exact origin |
| `apps/mobile/app.json` | the app's name, scheme and bundle identifiers, if you ship mobile |
| `apps/marketing/src/config/` and `src/content/` | the landing page, pricing and blog copy |
| `PLANS` and `FREE_PLAN_FEATURES` in `backend/product/config.py` | each plan's limits, matching the pricing page. `seats`, storage and `max_upload_mb` (per file, in MB of 1024 × 1024 bytes) are enforced, and a plan with no `seats` is unlimited. Nothing enforces the sample `api_calls` and `advanced_reports`, so promise them only once your product does |

Turn modules on and off in the same two config files, then run the module matrix on
that pull request by adding the `module-matrix` label.

**Expected:** `bash scripts/check-product-identity.sh https://<your site>` prints
nothing except what you have not done yet. The deploy runs the same check.

### 3. Replace the legal pages

`apps/marketing/src/content/legal/en/` holds placeholders. Have a lawyer write or
review the versions you publish, for your product, your jurisdiction and your
processors, then replace both files.

**Expected:** the check in step 2 stops refusing them.

### 4. Connect the real services

| Service | What it blocks without it | Where |
|---|---|---|
| Resend | Sign-up: nobody can verify an email | `RESEND_API_KEY`, and verify your domain with Resend |
| Stripe, live mode | Taking money | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `stripe-usage-pricing.md`, `billing-drift.md` |
| Google and GitHub sign-in | Those buttons | The OAuth client IDs and secrets, with `https://<your app host>/api/v1/auth/oauth/<google or github>/callback` as the redirect |
| Sentry | Finding out about errors | One project for the backend and one for the browser |
| Cloudflare Turnstile | Bots signing up, and sending magic-link and password-reset mail to any address | A Turnstile widget in Cloudflare, with your app host **and your API host** as allowed hostnames (the mobile app's check runs from the API's origin). `TURNSTILE_SITE_KEY` in `deploy/<environment>.env`, `TURNSTILE_SECRET_KEY` in Bitwarden. Both or neither: one alone stops the backend starting |

Sign-up and email change also refuse disposable email domains, from the bundled list in
`backend/app/modules/auth/disposable_domains.txt`. Add or exempt domains with
`BLOCKED_EMAIL_DOMAINS` and `ALLOWED_EMAIL_DOMAINS` in `backend/product/config.py`, and
refresh the list now and then with `python scripts/update_disposable_domains.py` (from
`backend/`).

Use the staging server (`staging.md`) to try these before live mode: its own Stripe
sandbox, Google client and Resend key.

**Expected:** you sign up on the real domain, get the email, and pay with a real
card, then refund it.

### 5. Set up the server

Follow `new-server.md`, including everything in its verification list.

**Expected:** a green deploy, and its checks passing on the box.

### 6. Prove it before you launch

- **Restore:** run the drill in `backup-restore.md`.
- **Scans:** run ZAP's full scan, and the TLS check against your domain
  (`.github/workflows/zap-full.yml` says what it needs).
- **Rate limits:** the 5000 an hour per organization is an estimate. Check how many
  429s real traffic gets.
- **Security review:** before real customers' money or data, pay for a penetration
  test (`pentest-prep.md`).
- **Bundle budgets:** your screens will replace the template's, so reset the numbers
  with `pnpm --filter web bundle:check --update` and review the diff (`bundle-budget.md`).
- **Recovery keys:** keep a second copy of the backup private key and anything else
  only one person holds.
- **A second person:** when one joins, require 1 approving review on `main` and add
  them as a required reviewer on the `production` environment. Until then a merge is
  a deploy (`docs/security/sdlc.md` §3).
- **Mobile:** if you ship it, upgrade Expo first, with a Maestro run on a Mac. The app
  is on SDK 52, and `.trivyignore.rego` and `.trivyignore.yaml` list what that clears.
  `security` goes red from 2027-01-01 until that is done or the dates are moved, even
  if you do not ship mobile.

**Expected:** each has a date and a result written down.

## Verification

The product's own name appears on the app, the marketing site and in an email.
`https://<your site>/robots.txt` allows crawlers, and `curl -sI` on the site shows no
`X-Robots-Tag`.

## Rollback

Everything here is a config change, so revert the commit and deploy. Identity already
indexed by a search engine or sent in an email stays out there, so do this before
launch.

## Escalation

- **The deploy says `refused:`.** Do what the message says. The check is there because
  each of these fails silently otherwise, so don't remove it to get a deploy through.
- **Legal questions.** Ask a lawyer. This template's pages are not legal advice.
