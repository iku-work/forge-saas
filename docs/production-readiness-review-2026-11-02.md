# Production readiness and security review: 2 November 2026

**Verdict at the time of review: not ready to start a real product yet, but close.** Six findings should be fixed first. One is a real account takeover in the auth code. The others are least-privilege and supply-chain gaps that get more expensive to fix after launch. The drill, backups, tenancy controls and edge all held up.

## Status

| Finding | State |
|---|---|
| H1, H4, M1 | Fixed in the auth PR that adds this file. |
| H1's purge job | Not built. Binding the password to its link removes the security need, so old unverified rows are now a data-retention question only. |
| H5 | Fixed in the CI pinning PR. |
| H2 | Closed on forge-prod-2: the firewall is attached, and every Hetzner deploy now fails if the origin answers directly. |
| H6 | Closed: secret scanning and push protection are on. |
| H3, Redis password from M3 | Closed on forge-prod-2: the app runs on its limited accounts, and `check-least-privilege.sh` passed there on 3 November. |
| M3's network split, M4 | Closed on forge-prod-2: `check-least-privilege.sh` passed there on 3 November with the network and filesystem checks. |
| M11, ZAP | Run on 3 November on a local copy of the production stack, not on forge-prod-2: through Cloudflare it would have tested the WAF and the rate limits, not the app. About 28,000 attack requests, no 5xx, nothing above Low. It reached 25 of the 102 operations, because routes with other IDs in the path stop at validation or a 404. |
| M11, TLS | Run on the public host on 3 November: Cloudflare's defaults still accepted TLS 1.0 and 1.1 and offered older CBC ciphers. Fixed on 4 November: 1.0 and 1.1 are refused from outside, and `new-server.md` sets the minimum version. Headers, the HTTP redirect and the hidden paths are right too. |
| M11, k6 | Run on the same local stack on 3 November, with pgbouncer and TLS. One backend process holds about 43 requests a second within budget, and uses a whole core at about 50, with no errors. No deployment needs more yet; more means more uvicorn workers. On GitHub's runner, without pgbouncer or TLS, all five scripts pass: `mixed` has a 90 ms p95 at 20 users. That run was repeated on 4 November, after `login-burst`'s budget became 1 second. The nightly load test and ZAP scan skip themselves until `STAGING_URL` is set, so nothing repeats these numbers before a product has a staging server. |
| M5, Sentry test | Found on the way: the workers never started Sentry, so no failed job or `capture_exception` in a job could reach it. Fixed in the worker Sentry PR. Proven on forge-prod-2 on 4 November: `check-sentry.sh` produced an issue from each of the three processes. |
| M5, the rest | Built in the first-product PR: `check-box-health.sh` reports a full disk or a failing container to Sentry hourly, and `new-server.md` adds an uptime monitor. Deployed to forge-prod-2 on 4 November, where the script's test event reached Sentry. Its disk and container triggers are only proven by tests. The hourly timer comes from the bootstrap, which has not run on a server yet, so forge-prod-2 has no timer and no uptime monitor. The exporters are still not scraped, deliberately: Prometheus is optional. |
| M10 | Closed: the Render and AWS deploys are removed. Hetzner is the one path, and it has run. |
| M6, M7 | Closed. A new server reboots for patches through the bootstrap, though the bootstrap's step has not run on a server yet. A manual reboot of forge-prod-2 on 4 November came back by itself in about 50 seconds, with `check-least-privilege.sh` passing. The CSP was tested in a browser on the live app: storage, Sentry and GitHub's avatar host were allowed, other hosts blocked. |
| M9 | Built in the first-product PR: `first-product.md` is the checklist, and a deploy from any repository but the template refuses the template's name, support address, indexing origin and legal pages. That check is skipped in this repository, so it is only proven by tests until the first fork deploys. |
| Storage console | Found on the way: browsers opening the storage host's root were redirected to MinIO's console, which exposed an internal port number. The console is now off, and on 4 November that address answered 403 on forge-prod-2. |
| Everything else | Open. |

The race H1 mentions is closed by the binding: an attacker registering again cannot change what the owner's link does. One risk remains: the owner clicking a link from a signup they never made. The email's footer already tells people to ignore it in that case.

## How this was checked

- **Live:** ordinary requests against the deployment on forge-prod-2, including a rate-limit test around Cloudflare. Read-only inspection of the box as the `forge` user.
- **CI:** repository settings, workflow files, and the coverage and security results of the latest `main` run.
- **Code:** the high-risk paths read in full (sign-in, OAuth, magic link, password reset, invitations, seeds), plus searches of the whole backend for banned patterns.

**This is not a penetration test.** Nothing was actively exploited, and not every line was read. H1 and M1 come from reading the code, not from a live attack. Mobile was not reviewed in depth. Database tests could not run locally because Docker is no longer on this machine, so they rely on CI.

## Fix before the first product

### H1. An account can be taken over before its owner signs up
`backend/app/modules/auth/service.py:732` and `:217`

1. An attacker registers `victim@gmail.com` with their own password. The account sits unverified, holding the attacker's password hash.
2. The victim later signs in with Google. `oauth_login` links the account and marks it verified, but keeps the attacker's password. The attacker can now log in as the victim, and can enroll their own second factor.

A second route gets there too. If the victim registers normally, `register` ignores their password and resends verification. Clicking that link activates the attacker's password.

Nothing removes unverified accounts either, so these rows wait indefinitely.

**Fix:**
- When a provider verifies an unverified account, clear `hashed_password`.
- On re-registration, replace the pending hash and invalidate older verification tokens. One small race remains: an attacker re-registering between the victim's request and their click. Binding the password hash to the verification token closes that too.
- Add a job that purges long-unverified accounts.
- Regression test for each route.

### H2. The origin answers directly, around Cloudflare
From a machine outside Cloudflare's ranges, the box returned 200 on 443 and 301 on 80 when addressed directly. `new-server.md` says to allow only Cloudflare's ranges, so the Hetzner firewall is missing, not attached, or not restricting these ports.

Anyone who finds the IP skips Cloudflare's WAF and DDoS protection. Spoofing is not possible: IP headers sent this way were ignored, and the limiter still returned 429.

**Fix:**
- Check the firewall in the Hetzner console (only the operator can see it).
- Add a deploy check from the GitHub runner, which is outside Cloudflare's ranges: a direct connection to the origin must fail. That turns the runbook step into something enforced.

### H3. The app has more database and storage power than it needs
- **Postgres:** `DATABASE_URL` connects as `postgres`, a superuser. Any SQL injection then reaches `COPY ... PROGRAM` and every database. No injection is known and the code uses the ORM, but this removes the second line of defence.
- **MinIO:** the app's access key is the MinIO root user (matching hashes).

**Fix:** have the bootstrap create a limited app role and a scoped MinIO user, and keep the owner role for migrations only. Do this before a product has data.

### H4. Seed scripts can create a superadmin with a published password
`backend/app/core/seed.py:25`

`python -m app.core.seed` has no environment check. It creates `admin@example.com` as superadmin, with a password that is in this public repo. Whoever signs in first could then enroll their own second factor. `seed_pentest.py` and `seed_load.py` have no check either, but they generate random passwords.

**Fix:** refuse to run when `ENVIRONMENT=prod`. Every committed env file CI uses says `dev`, so this should not break CI. Use `make sync-plans` for plans in production.

### H5. CI runs code it does not pin
The deploy job holds every production secret, and the May 2026 `@tanstack` compromise (AGENTS.md §0.12) shows a trusted publisher can ship malware with valid provenance.

- All actions use movable tags, not commit SHAs. That includes `docker/login-action@v3`, which runs inside the deploy job. `chromaui/action@latest` is the loosest.
- Three installers are fetched from a project's `main` branch and piped to `sh`: trufflehog (`ci.yml:1045`), and trivy twice (`ci.yml:1096`, and `ci.yml:1291`, in the `build` job, which has `packages: write`). The Maestro one has no version at all (`maestro-mobile.yml:99`).

**Fix:** pin actions to SHAs (Dependabot keeps those updated), and download versioned installers with a checksum, as the Bitwarden CLI step already does.

### H6. GitHub secret scanning is off on a public repo
Secret scanning and push protection are both disabled. Gitleaks and trufflehog run in CI, but only after a secret has already been pushed.

**Fix:** turn both on in repository settings. It takes a minute and is free for public repos.

## Fix soon

| # | Finding | Fix |
|---|---|---|
| M1 | **Invitation links work for anyone signed in.** `accept_invitation` never compares the user to `invitation.email`, so a forwarded link grants its role, even admin. Acceptance is not atomic, unlike the other single-use tokens, and an existing member gets a 500 from the unique constraint. | Require a matching email, consume atomically, return a clean error. |
| M2 | **Plan seat limits are not enforced.** Plans advertise `seats: 3` and `seats: 25`, but nothing outside the seed reads them. | Enforce at invite and accept, or drop the promise. |
| M3 | **Redis has no password**, only TLS, and every service shares one network including the internet-facing Caddy. ARQ uses pickle by default, so reaching Redis could mean code execution in the worker. | Set a Redis password, split the networks. |
| M4 | **Containers are not hardened.** No `cap_drop`, `no-new-privileges` or read-only filesystems. `postgres:16-alpine`, `redis:7-alpine` and `clamav:stable` are floating tags. The backend runs as non-root, which is good. | Add the options service by service, and pin by digest. |
| M5 | **Almost nothing would alert you.** The exporters run but nothing scrapes them, so disk and worker alerts cannot fire. There is no uptime check, and `new-server.md` does not mention one. Backend errors reaching Sentry is unconfirmed. Only the backup monitor is proven. | Add an uptime check and a metrics step to the runbook, and send a test Sentry event. |
| M6 | **Kernel patches wait forever.** The box has a pending `reboot-required`, and neither the box nor the bootstrap sets an automatic reboot. | Decide on a reboot window, and set it in the bootstrap. |
| M7 | **The CSP allows `connect-src https:` and `img-src https:`,** so an XSS could send data anywhere. The Caddyfile says a deployment should narrow these, but `new-server.md` does not say so. | Add it as a step, or derive it from `STORAGE_DOMAIN`. |
| M8 | **Three node-tar exceptions expire on 2026-12-01.** `security` is a required check, so merges stop that day. | Upgrade `@expo/cli`, or decide to extend. |
| M9 | **A fork inherits this deployment's identity.** `product.config.ts` says ForgeSaaS and `support@example.com`, and `PRODUCTION_ORIGIN` is `mycalendar.party`. Nothing refuses these in production, and there is no first-product checklist. The legal pages need review. | Write the checklist, and refuse the defaults at deploy. |
| M10 | **Render and AWS deploys have never run,** yet the template offers them. | Label them unproven, or remove them. |
| M11 | **ZAP full scan, TLS check and k6 have never run** against a real deployment. DNS now makes that possible. | Run each once before teardown. |

## Minor

- **Coverage:** AGENTS.md §0.8 requires 100% on `auth/`, `billing/`, `tenancy/` and `gdpr/`. Actual is 97-99% in several files, and CI enforces only 85% overall (96.17% today). Enforce it or amend the rule.
- **JWTs** carry no `iss` or `aud`. This matters only if a key is ever shared between products.
- **`/minio/health/live`** is public on the storage host.
- **`except Exception`** appears in about a dozen places, each explained, against §0.5's "single top-level handler". The rule should match reality.
- **Single-person controls:** PRs need 0 approvals and `production` has no required reviewers, so a merge is a deploy. That is fine alone; change it when a second person joins.
- **`forge` is in the `docker` group,** so CI's SSH key is effectively root on the box.
- **Two moderate Dependabot alerts** (`glob`, `minimatch`) are transitive. They appear to come from build and test tooling only, which is not confirmed.
- **Stale docs:** the security baseline's V8.1.5 and V8.1.6 still say `partial`.

## An incident during this review

While listing setting names on the box, one of my commands printed several lines from the middle of forge-prod's `JWT_PRIVATE_KEY`, because the value spans multiple lines. They are now in this session's transcript. It affects only this throwaway deployment, which has no real users. **Never reuse that key,** and rotate it if the box stays up longer than planned.

## What held up

- **Edge:** all security headers present on app, API and marketing. `/metrics`, `/docs`, `/redoc` and `/openapi.json` return 404. Paths like `/.env` return the app's HTML, not files.
- **CORS:** a foreign origin gets no `Access-Control-Allow-Origin`, and its preflight gets a 400.
- **Rate limiting:** cut off at the limit, and spoofed IP headers were ignored even when connecting directly.
- **Storage:** bucket listing and direct object paths return 403.
- **Box:** SSH is key-only, only Caddy publishes ports, the env files and key file are mode 600, and no private backup key is present.
- **Backups:** the nightly run succeeded, and archives are copied off the box. Restores were drilled on 2026-10-31.
- **Deploy pipeline:** all three failure cases passed on 2026-11-02.
- **Tokens:** password reset, magic link and email verification are atomic and single-use. A reset revokes all sessions. Second factors are checked on every sign-in path.
- **OAuth:** state is signed and tied to a cookie, and provider-verified email is required.
- **Sessions:** refresh cookies are `httpOnly; Secure; SameSite=Strict`. The web app stores no tokens in `localStorage`.
- **Code patterns:** no `shell=True`, `verify=False`, `eval` or `pickle` calls in the backend. Raw SQL is limited to bound or constant statements. No `OFFSET` pagination, no `HTTPException` in services.
- **CI security job:** trivy, pip-audit, pnpm audit, semgrep and secret scans all passed on the latest run. Coverage is 96.17%.

## Suggested order

1. **Today, in settings:** H6, and check the firewall for H2.
2. **Auth PR:** H1, H4 and M1, with regression tests.
3. **CI PR:** H5, plus the origin check from H2.
4. **Infrastructure PR, proven on forge-prod-2 before teardown:** H3, then M3 and M4.
5. **Scans before teardown:** M11, and the Sentry test from M5.
6. **Fork kit:** M9, M5's runbook step, M6, M7 and M10.
7. **M2 and M8,** as decisions: seats depend on the first product's pricing, and node-tar depends on when mobile work starts.
