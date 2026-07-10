# Secure SDLC policy

T-215. Where security fits into planning, review, and release for this
template. This document doesn't invent a new process — it names the gates
that already run in CI (`.github/workflows/ci.yml`,
`.github/workflows/zap-full.yml`) and says at which stage each one fires,
so "is security in the SDLC" has a concrete, auditable answer instead of
"we're careful."

## 1. Planning

- A design change that touches authentication, session handling,
  cryptography, tenant isolation, or a new remote service integration
  updates [`docs/security/threat-model.md`](threat-model.md) in the same
  PR — new trust boundary, new arrow on the data-flow diagram, or both.
- A design change that introduces a new class of stored data adds a row to
  [`docs/security/data-classification.md`](data-classification.md) (T-219)
  in the same PR: treat any new PII-shaped column as `sensitive` by default
  and encrypt it (`backend/app/core/encrypted_type.py`) unless there's a
  documented reason not to.
- There is no separate per-sprint threat-modeling ritual — ASVS-baseline
  review (below) plays that role on a slower cadence, and this per-change
  check plays it on every change that actually needs it. Full re-review of
  [ADR-0001](../adr/0001-security-baseline.md) happens when a new ASVS
  chapter becomes applicable (e.g. hardware auth going from `n/a` to
  in-scope) or roughly once a year, whichever comes first.

## 2. Development

- [`docs/security/owasp-top10.md`](owasp-top10.md) and
  [ADR-0001](../adr/0001-security-baseline.md) are the secure-coding
  reference — each row names the mitigation pattern already in the
  codebase (org-scoped RBAC, parameterized queries via the ORM, output via
  `Envelope`/`jsonable_encoder`, etc.) so new code extends an existing
  pattern instead of inventing one.
- Secrets never go in source. `backend/app/core/config.py`'s `Settings`
  is the only place environment variables are read (§0.6); CI's
  `security` job runs trufflehog and gitleaks on every push as a backstop,
  not the primary control.
- New dependencies are expected to occasionally fail `pip-audit`/`pnpm
  audit`/`trivy` — that's the point of running them before merge, not a
  bug in the tool.

### Input/output handling policy

One policy, applied uniformly rather than decided per-route:

- **Input**: every request body is a typed Pydantic schema
  (`backend/app/core/schemas.py`'s `AppBaseModel`) — allow-listed fields
  only (unknown fields are silently dropped, not merged), explicit length/
  type constraints per field, and a null-byte guard (`_reject_null_bytes`)
  applied across the board. There is no data type accepted by this API
  that bypasses schema validation.
- **Output**: every response is JSON via the `Envelope` shape
  (`backend/app/core/handlers.py`) and FastAPI's `jsonable_encoder` — one
  serialization boundary, not per-route encoding logic.
- **Files** (the one binary data type this API handles) never pass
  through this JSON boundary at all — see
  [`threat-model.md`](threat-model.md) §3 on presigned-URL upload/
  download.

## 3. Code review

A reviewer approving a PR that touches any of the following confirms the
matching control wasn't bypassed, not just that the diff "looks right":

| Touches | Reviewer checks |
|---|---|
| A new route | `require_permission`/`get_current_org` present, not a hand-rolled check (V1.4.4) |
| A new DB column holding PII/secrets | `EncryptedString` used, or a documented reason it's plaintext (V1.8.1/V6.1.1) |
| A new outbound HTTP call | Routed through `validate_outbound_url()` if the target is ever user-influenced (V5.2.6) |
| A new log statement | No raw secret/PII interpolated outside the `extra={}` dict `_scrub()` can reach (V7.1.1) |
| A new external integration | [`threat-model.md`](threat-model.md) updated with the new trust boundary |

This is a checklist for the reviewer's attention, not a gate a bot
enforces — the CI gates in the next section are what's actually blocking.

## 4. CI gates (automated, blocking)

Every one of these runs on every push/PR via the `security` job in
`.github/workflows/ci.yml`, and each is a hard failure (no
`continue-on-error`) that blocks the `build` job:

| Tool | Catches | Job step |
|---|---|---|
| trufflehog | Committed secrets, full git history | `security` → "Trufflehog (git history secret scan)" |
| gitleaks | Committed secrets, working tree | `security` → "Gitleaks (working-tree secret scan)" |
| trivy | Filesystem/dependency CVEs + secrets, HIGH/CRITICAL | `security` → "Trivy filesystem scan" |
| bandit | Python security anti-patterns (medium+ severity) | `security` → "Bandit" |
| semgrep | Community rules + project-custom rules (`backend/semgrep/`: no-os-environ, no-mass-assign, no-plaintext-secret-column) | `security` → "Semgrep" |
| pip-audit | Python dependency CVEs | `security` → "pip-audit" |
| pnpm audit | Node dependency CVEs (high+) | `security` → "pnpm audit" |
| Schemathesis | Live fuzz of every documented endpoint against a running backend | `test` → "Schemathesis live fuzz" |
| ZAP baseline | Passive DAST against a throwaway docker-compose stack | `zap-baseline` job |

`build` additionally re-scans the built container image with trivy before
it's pushed (`.github/workflows/ci.yml` `build` job).

## 5. Pre-release / staging

- **ZAP full active scan** (`.github/workflows/zap-full.yml`) runs
  nightly against staging — active attack payloads (SQLi, XSS, command
  injection) that are deliberately not run against every PR. See T-211's
  notes; this requires `STAGING_URL`/`ZAP_SCANNER_EMAIL`/
  `ZAP_SCANNER_PASSWORD` to be configured before it does anything.
- Before commissioning a manual pentest or bug-bounty engagement, follow
  [`docs/runbooks/pentest-prep.md`](../runbooks/pentest-prep.md) — scope,
  test accounts, and known-`partial` ADR rows the engagement should
  focus on.

## 6. Release

- The `build` job only runs after `test` and `security` both pass
  (`needs: [test, security]` in `.github/workflows/ci.yml`) — there is no
  path to a pushed image that skipped the security gates.
- Deploys are documented in
  [`docs/runbooks/deploy-render.md`](../runbooks/deploy-render.md).

## 7. Ongoing

- [`docs/security/owasp-top10.md`](owasp-top10.md) is machine-checked
  (`backend/tests/security/test_owasp_mapping.py`) — a category can't go
  stale silently because the test fails if its evidence file/test
  disappears.
- [ADR-0001](../adr/0001-security-baseline.md) is link-checked the same
  way (`backend/tests/security/test_security_baseline.py`) but its
  `met`/`partial` status is maintained by hand — update it in the same PR
  that closes a `partial` row's follow-up task.
