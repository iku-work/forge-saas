# Phase 4 acceptance runbook

The gate out of Phase 4: the module system extended to the build, a public
marketing site, org-scoped API keys, outbound webhooks, and enterprise SSO —
working together on a live stack, not merely green in CI.

Phase 4 is the first phase whose output is visible to people who are **not
users**: crawlers, integrators and IdP admins. So this document is written to
be executable by somebody who did not build any of it. Where a step says "see
X", read X — a copy of X here is a copy that will be wrong in three months.

Budget most of a day. Several steps need a live stack; two need credentials
this repo cannot contain (a Stripe test key, an Okta or Entra dev tenant).

**Three of Phase 4's five modules ship off.** `public_api`,
`outbound_webhooks` and `enterprise_sso` are disabled in the template's own
`product/config.py`, so steps 6, 7 and 8 need a stack booted with them on.
Step 1 says how, once, and the later steps assume it. A sub-phase whose module
you have chosen to leave off passes by having its **absence** verified — that
is a pass, not a skip, and step 9 is where it is checked.

## Trigger

- Before declaring Phase 4 complete.
- After a change that spans sub-phases — a new module toggle, a change to
  `app/core/principal.py`, an upgrade of Astro or authlib — where each area's
  own tests pass but nothing has exercised them together.
- Before publishing the API contract or the marketing site to a real domain
  for the first time. Steps 3 and 6 are the ones that stop a bad
  `robots.txt` or an over-scoped key reaching the public.

**Not this runbook:** a single failing area. Go to its own runbook —
[api-keys.md](api-keys.md), [outbound-webhooks.md](outbound-webhooks.md),
[sso-onboarding.md](sso-onboarding.md),
[module-toggle-guide.md](module-toggle-guide.md),
[analytics-and-consent.md](analytics-and-consent.md) — or its test suite. This
document is for the integrated pass only, and it assumes
[phase3-verification.md](phase3-verification.md) still passes.

## Pre-checks

Each one is here because skipping it wastes an hour later.

- **`backend/.env` filled in for real.** `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`,
  `ENCRYPTION_KEY`, `EMAIL_BACKEND=smtp`. Not the example values.
- **A valid `sk_test_...` Stripe key.** `outbound_webhooks` *requires*
  `billing`, so step 7 cannot run without one. Check it first with
  `stripe balance retrieve`.
- **Docker with headroom.** This pass boots the dev stack, an ephemeral stack
  for ZAP, testcontainers for pytest, k6 and a local webhook receiver. 8 GB is
  not enough on Windows.
- **An Okta or Entra dev tenant**, for step 8. There is no way around this:
  the mock IdP in `backend/tests/modules/test_sso.py` signs with real keys and
  proves the protocol, but it cannot prove that a *particular vendor's*
  discovery document and claim shapes are what this client expects. If you do
  not have one, run step 8's other halves and record the first as not done.
- **A clean `git status`.** Step 1 overwrites `product/config.py` and
  `product.config.ts`; you want to be able to put them back.
- **Node 22 and pnpm**, matching CI. The repo pins `@types/node` to one
  version on purpose (`pnpm-workspace.yaml`) — see that file's comment.

## Steps

### 1. Boot everything, with the Phase 4 modules on

The template's own config leaves three of them off, so use the fixture CI's
`module-matrix` job already proves boots and migrates rather than editing the
config by hand:

```bash
cp backend/tests/fixtures/module_configs/phase4.py backend/product/config.py
cp backend/tests/fixtures/module_configs/phase4.config.ts product.config.ts

make dev
make seed
pnpm --filter marketing dev     # a second terminal
```

**Expected:** `GET /api/v1/config` reports `public_api`,
`outbound_webhooks`, `enterprise_sso` and `marketing` all `true`. If the boot
fails with a `RuntimeError` naming two modules, that is `validate_modules()`
doing its job — `enterprise_sso` requires `oauth` and `outbound_webhooks`
requires `billing`.

**Put these two files back at the end of the pass.** They are the product's
own configuration, and this runbook borrowed them.

### 2. Marketing renders, in more than one locale

Landing in `en` and in `fi` through the locale path; nav, pricing, features, a
blog post and a guides page; footer legal links resolve.

`fi` is deliberately the incomplete locale (see `packages/i18n/README.md`), so
this step is also the graceful-fallback check: Finnish where translated,
English where not, and **no key names on screen**.

**Expected:** every page renders, `/` redirects to a locale prefix rather than
serving content (T-305 — every URL is `/en/...`), and no console errors.

### 3. SEO, on the production build

```bash
pnpm --filter marketing build     # check-seo.mjs + check-indexing.mjs gate this
```

Then, against the built output:

- `sitemap.xml` validates and every URL in it 200s.
- `robots.txt` is correct **for the host it is served from**: indexing is
  opt-in via `PRODUCTION_ORIGIN` (T-304), so a staging build must say
  `Disallow: /`. Check the negative case, not only the positive one.
- `hreflang` alternates resolve **in both directions** — `check-hreflang.mjs`
  asserts the cluster, but follow two by hand.
- Rich Results Test clean on landing, pricing and a post. The offline gate
  (T-303) checks the shapes; this checks Google agrees.

**Expected:** the build's own gates pass, and the four manual checks agree
with them. A build that passes `check-seo.mjs` while serving a `noindex` you
did not intend is the failure this step exists to catch.

### 4. Performance budget

```bash
pnpm --filter marketing lighthouse
```

**Expected:** four pages × three runs, median within budget — LCP, CLS,
TBT-as-INP-proxy, SEO ≥ 0.95, a11y = 1.0. On Windows an occasional `EPERM`
on `...\Temp\lighthouse.NNNNNNNN` is chrome-launcher racing its own profile
cleanup, not a budget failure -- `apps/marketing/scripts/lighthouse.mjs`
explains it at the point that handles it.

### 5. Consent: the default is *no*

Load the marketing site fresh, in a private window, with devtools open.

**Expected, in the default configuration:** **zero** cookies set and **zero**
third-party requests. Not "only analytics" — zero. T-308 ships with neither
analytics nor a cookie banner enabled, because a template that phones home by
default makes that decision for every product built on it.

Then turn one on (`analytics-and-consent.md`) and confirm the banner appears,
that declining it keeps the count at zero, and that accepting it is what
starts the requests.

### 6. API keys, end to end and then switched off

With the stack from step 1:

1. Create a key in org settings. **The secret shows exactly once** — copy it.
2. Call a scoped endpoint with it. Put the key in the environment rather than
   on the command line — it is a live credential, and a literal here ends up in
   your shell history and in anything you paste this into:
   ```bash
   export THE_KEY=...            # the value shown once in step 1
   curl -H "Authorization: Bearer $THE_KEY" -H "X-Org-ID: $ORG_ID"      https://localhost/api/v1/orgs/$ORG_ID
   ```
3. Call an endpoint the key's scopes do not cover → **403**.
4. Exceed the budget → **429**, with `X-RateLimit-Limit`, `-Remaining` and
   `-Reset`. They are on the **success** path too, which is the half worth
   checking: an integrator can only back off before being refused if a 200
   already tells them how much budget is left.
5. Revoke it → **401**, immediately.

Then the module half:

```bash
cp backend/product/config.py /tmp/phase4-config.py     # keep it
# set public_api=False in backend/product/config.py, then:
make dev
```

**Expected:** the *same* key now gets **401** and `/api/v1/api-keys` is
**404**. Two different mechanisms, and both matter: the routes go with the
toggle like any module's, but a key's ability to authenticate at all is
checked in `app/core/principal.py` — so a key minted while the module was on
stops working the moment it is off, even though its row still exists.

Restore the config before continuing.

### 7. Webhooks, including the part where the consumer is down

Point an endpoint at a local receiver (`nc -l 9000`, or any request-bin), then:

1. Emit an event; confirm delivery and check the signature against
   `app/modules/webhooks/signing.py`'s `verify_signature`.
2. **Stop the receiver.** Watch the retry schedule advance — 1 m, 5 m, 30 m,
   2 h, 12 h. Do not wait it out: read `next_attempt_at` on the row, which is
   what the queue schedules from.
3. Restart the receiver and confirm the delivery lands.
4. Replay a delivered event. Confirm the payload is **byte-identical**, that
   it arrives with a **new** `X-Forge-Delivery`, and that the original row is
   untouched.

**Expected:** all four. Step 4 is the one worth being fussy about — a replay
that mutates the original row destroys the evidence an incident review needs.

### 8. SSO, against a real IdP

Follow [sso-onboarding.md](sso-onboarding.md) against your dev tenant:

1. Connect it to a test org.
2. Sign a **new** person in → JIT-provisioned with `default_role`, and
   *linked* rather than duplicated if the email already existed.
3. Enable enforcement → that org's members are refused a password with
   `errors.sso_required`.
4. Confirm the **break-glass**: the last remaining owner can still use a
   password. Not any owner — the last one.

**Expected:** all four. If you have no dev tenant, steps 3 and 4 still run
against a locally-seeded connection; record step 1–2 as not done rather than
ticking them.

### 9. Every module off, and the product still whole

```bash
git checkout backend/product/config.py product.config.ts   # back to defaults
# then set marketing, public_api and enterprise_sso all False
make migrate && make dev && pnpm build
```

**Expected:** the product boots, migrates and builds. `/api/v1/config` reports
all three `false`. `apps/marketing` produces **no `dist`** and the root build
stays green (T-300: `marketing` gates a *build*, not a router). The app host
is completely unaffected, and there is no marketing host at all — absence,
rather than a host that 404s.

The negative half matters as much: nothing 404s that should not. The app's own
routes are untouched by all three toggles.

### 10. Security re-scan

```bash
cd backend && python -m pytest tests/security tests/contract -q
```

**Expected:** green, and specifically:

- `tests/security/test_tenant_isolation.py` — the cross-tenant sweep, now
  including the API-key principal (T-311). The webhooks and SSO surfaces are
  swept in their own modules' files (`test_webhooks.py`, `test_sso.py`,
  each under "§0.8 non-negotiable 1"), because both ship off and their
  routers have to be stood up explicitly.
- `tests/contract/test_schemathesis.py` — two documents now. The app's own
  `/openapi.json`, and **the published contract** (T-314), fuzzed with the
  same checks. The second is what catches "the contract we hand integrators
  does not describe what we return"; it is built from
  `build_public_schema()` rather than fetched, so it runs whether or not
  `public_api` is on.

Then CI's DAST pass, which now covers both hosts: `zap-baseline` scans
`https://caddy` **and** `https://marketing.localhost`, as two reports rather
than one, and skips the second when `marketing` is off.

**Expected:** both baselines green. Any new entry in `.zap/baseline.conf`
must carry an owner and a dated justification — an undocumented ignore is not
allowed, and neither is an ignore added instead of reading a finding.

### 11. Load, on the path that is new

```bash
make load-test      # or: docker compose run --rm k6 run /scripts/api-key.js
```

**Expected:** all five scripts within budget, **including `api-key.js`**. It
is new in Phase 4 and it is not redundant: a session token costs one signature
check, while a key costs a lookup by prefix plus a constant-time digest
comparison on **every** request, before any handler runs. That is the shape of
path T-223 found the argon2-on-the-event-loop bug hiding in.

It needs `public_api` on — the nightly workflow copies the phase4 fixture
before booting, and its `setup()` fails loudly rather than reporting a budget
it never measured.

## Verification

The pass is complete when all of the following are true — checked from
outside the step that produced them, not by trusting a step that said OK:

- [ ] Fourteen modules; `/api/v1/config` agrees with `product/config.py` in
      both the all-on and all-off configurations
- [ ] CI's `module-matrix` green on default, everything-off and Phase-4-on —
      backend **and** frontend
- [ ] `enterprise_sso` without `oauth` fails at boot with a message naming both
- [ ] Marketing: every page in every enabled locale, `check-seo.mjs` and
      `check-indexing.mjs` passing, hreflang resolving both ways, Lighthouse
      within budget on four pages
- [ ] Default marketing build sets **zero** cookies and makes **zero**
      third-party requests
- [ ] A key authenticates only on scope-declaring endpoints; revocation,
      expiry and module-disable each kill a live key
- [ ] The key is shown once and appears in no log, response or `repr()`
- [ ] Webhook signature verifies against a fixed vector and a mutated body
      fails; the retry ladder is observed; a replay leaves the original row
      untouched
- [ ] Endpoint secret and SSO `client_secret` are both `EncryptedString`,
      redacted in `repr`, and picked up by the key-rotation job
- [ ] SSO: JIT provision, link-not-duplicate, enforcement, break-glass
- [ ] ZAP baseline green on **both** hosts
- [ ] k6 budgets met on all five scripts
- [ ] `pytest --cov` thresholds not regressed; `api_keys` and `sso` at 100%
- [ ] ADRs 0004, 0006 and 0007 pass `backend/scripts/check_docs.py`, and
      `docs/adr/README.md`'s index is **regenerated**, not hand-edited
- [ ] Every new setting has a `.env.example` entry and a line in the deploy
      runbook
- [ ] `§7`'s cross-cutting checklist in `agents.md` ticked for anything changed
      while running this

Two of these can appear to pass while doing nothing, so check them from
outside: a **green CI badge can be stale** (read the run dates, not the
badge), and a **`robots.txt` can be correct on your machine and wrong on the
host that matters**, because it is generated per environment.

## Rollback

Nothing here changes production. Five steps leave state behind on the machine
you ran them on:

1. **Step 1** overwrites `backend/product/config.py` and `product.config.ts`
   with the phase4 fixtures. `git checkout` both — this is the one most likely
   to be committed by accident.
2. **Step 6** leaves API keys in the database, one of them revoked. Delete
   them, or leave them and note that you did: a live key from a verification
   pass is a real credential.
3. **Step 7** leaves webhook endpoints and a delivery log. The endpoint may
   also be **auto-disabled** if you left the receiver down through five
   dead-letters — re-enable it or delete it, and remember that re-enabling
   clears the failure counter deliberately.
4. **Step 8** leaves an `sso_connection` row and possibly JIT-provisioned
   users with **no password**. Turn `enforced` off *before* deleting anything,
   and check for those accounts:
   `SELECT count(*) FROM oauth_account WHERE provider LIKE 'sso:%'`.
5. **Steps 3, 4 and 9** rebuild `apps/marketing/dist`. Harmless, but it is
   gitignored output — do not be surprised by it.

## Escalation

**A step fails in an area you did not change.** Go to that area's runbook
first. Several suites here have documented, pre-existing failure modes: the
`ar` RTL visual snapshot, T-211's unvalidated ZAP alert filters, and anything
Stripe-touching with an expired key.

**A test passes alone and fails in the full suite.** Three known traps in this
codebase produce exactly that, all documented rather than mysterious: a
`logging.config` call that disables every `app.*` logger for the rest of the
process (which makes `caplog` assertions vanish); settings bound at import
time from `backend/.env` before conftest applies `.env.test`; and a globally
patched `httpx.AsyncClient` or `socket.getaddrinfo`, which redirects the test
suite's *own* client and produces an empty 404 from a route that plainly
exists. Check those three before bisecting.

**A just-regenerated symbol "doesn't exist" in the frontend.** Three
generated, gitignored artefacts need rebuilding by hand after a schema or
locale change — `packages/api-client/src/generated/`,
`packages/api-client/openapi.json` and `packages/i18n/src/keys.ts` — and turbo
has been known to serve a stale `dist` for the first. Regenerate and rebuild
before believing the error.

**A module-matrix leg fails on a route or table you added.** A new route under
a module toggle needs a `MODULE_SURFACE` entry and a new table needs a
`MODULE_TABLES` one; without them, five unrelated matrix cases fail with
messages that do not mention your change. See
[module-toggle-guide.md](module-toggle-guide.md).

**You cannot complete the pass.** Record which steps ran, which did not, and
why. A partial pass with an honest list is worth more than a complete one that
quietly skipped the parts needing a dev tenant or a Stripe key. **Do not tick
a box in Verification for a step you did not run** — the boxes are the record,
and a wrong one is worse than an empty one.
