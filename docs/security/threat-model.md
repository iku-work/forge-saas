# Threat model

T-215. Trust boundaries and data flows for the components ASVS V1.1.4/
V1.1.5 expect documented on their own, not scattered across `agents.md`
prose. Paraphrased from the architecture described there — see `agents.md`
for the authoritative module-by-module detail.

## 1. Data-flow diagram

```
                                   ┌─────────────┐
                                   │   Browser    │
                                   │  (apps/web)  │
                                   └──────┬───────┘
                                          │ HTTPS (TLS 1.2/1.3, Caddy-terminated)
                                          ▼
                                   ┌─────────────┐
                                   │    Caddy     │  (1) reverse proxy, auto TLS
                                   └──────┬───────┘
                                          │ HTTP, internal docker network
                                          ▼
                                   ┌─────────────┐
                          ┌────────┤   Backend    ├────────┐
                          │        │  (FastAPI)   │        │
                          │        └──┬───┬───┬───┘        │
                          │           │   │   │            │
                 (2) DSN  │  (2) DSN  │   │   │ (3) presigned URLs
                          ▼           ▼   │   ▼
                    ┌─────────┐ ┌────────┐│ ┌──────────────┐
                    │Postgres │ │ Redis  ││ │ Object storage│
                    │         │ │        ││ │ (S3/R2/MinIO) │
                    └─────────┘ └────────┘│ └──────────────┘
                                          │
                    (4) credentialed HTTPS, outbound only
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                    ┌─────────┐    ┌───────────┐   ┌──────────────┐
                    │  Stripe  │    │  Resend    │   │ Google/GitHub│
                    │ (billing)│    │  (email)   │   │   (OAuth)    │
                    └─────────┘    └───────────┘   └──────────────┘

                    ┌─────────────┐
                    │    Worker    │  same trust level as Backend — shares
                    │    (ARQ)     │  Postgres/Redis, makes the same outbound
                    └─────────────┘  calls (email sends, webhook delivery)
```

A mobile client (`apps/mobile`) is a second, symmetric entry point at
boundary ① — same API, same auth, no separate trust path.

## 2. Trust boundaries

| # | Boundary | Crosses from → to | Control |
|---|---|---|---|
| ① | Public internet → Caddy | Untrusted client → edge | TLS termination, HSTS (`Caddyfile.prod`); `/metrics` blocked externally |
| ① | Public internet → Caddy, marketing host | Untrusted client → edge | A second public hostname (T-307), serving `apps/marketing/dist` and nothing else: no backend behind it, no session, no `Set-Cookie`. Its own CSP, HSTS and `X-Robots-Tag` come from the site block that app's build generates. The app's refresh cookie is `__Host-` prefixed and host-scoped, so a browser never sends it here — deliberately, and asserted in `backend/tests/test_caddy_config.py` |
| ① | Caddy → Backend | Edge → application | Plain HTTP, but confined to the docker-internal network (`forge-net`) — never reachable from outside the compose network |
| ② | Backend/Worker → Postgres/Redis | Application → data store | Credentialed DSNs (`DATABASE_URL`, `REDIS_URL`), required with no default (`backend/app/core/config.py`) — see V1.9.1/V9.2.2 in [ADR-0001](../adr/0001-security-baseline.md) for the TLS-in-transit caveat on this hop |
| ③ | Backend → Object storage | Application → third-party storage | Files are never proxied through the backend; clients get short-lived presigned URLs (`backend/app/modules/storage/service.py`) and talk to S3/R2/MinIO directly |
| ④ | Backend/Worker → Stripe/Resend/OAuth providers | Application → third-party API | Outbound only, each authenticated with its own credential (`STRIPE_SECRET_KEY`, `RESEND_API_KEY`, `GOOGLE_CLIENT_SECRET`/`GITHUB_CLIENT_SECRET`); standard TLS cert verification, no `verify=False` anywhere in the codebase |
| — | Client ↔ Backend, within boundary ① | Anonymous → authenticated | The JWT/refresh-token pair (`backend/app/modules/auth/`); every other boundary in this diagram is reached only after this one is crossed |
| — | Third party ↔ Backend, within boundary ① | Anonymous → authenticated, **without a user** | An API key on the same `Authorization: Bearer` header, dispatched on the `fk_live_` prefix (`backend/app/core/principal.py`, module `public_api`, default off). A second kind of principal: no session, no person, and no account to disable. Three things bound it — a key reaches an endpoint only where that endpoint declares `require_scope(...)` (the whole surface is two reads, enumerated by a test), it is confined to the organization on its own row, and the module toggle is checked on the auth path so turning `public_api` off stops every existing key even though its row survives |
| — | Org A ↔ Org B, inside the Backend | Tenant → tenant | Not a network boundary — enforced entirely in application code (`require_permission`, `backend/app/modules/tenancy/service.py`'s `org_id` re-check). This is the boundary `backend/tests/security/test_tenant_isolation.py` exists to guard. |

## 3. Component notes

- **Backend and Worker are the same trust level.** Both run the same
  Docker image (`docker-compose.prod.yml`), share the same Postgres/Redis
  credentials, and make the same class of outbound calls. There is no
  internal service-to-service API surface between them requiring its own
  authentication (V1.2.2) — they communicate only via the ARQ job queue in
  Redis.
- **Object storage is out of the request path for file bytes.** The
  backend only ever issues presigned URLs; it never reads or writes file
  content itself outside of virus-scanning (`backend/app/modules/
  storage/scanner.py`). This is why V12.4.1/V12.5.2 are `met` — an
  attacker who compromises the backend still can't use it as a proxy to
  read arbitrary storage objects beyond what a presigned URL already
  scopes.
- **Third-party services are outbound-only from the backend's
  perspective**, except for two inbound webhook paths that re-enter
  through boundary ①: Stripe webhooks (signature-verified,
  `backend/app/modules/billing/`) and the OAuth redirect callback
  (state-token verified, `backend/app/modules/auth/`). Both are treated as
  untrusted input despite originating from a trusted provider — the
  signature/state check is what actually establishes trust, not the
  source IP or URL path.
- **Mobile and web clients are symmetric.** Neither is more trusted than
  the other; both authenticate the same way and are subject to the same
  rate limits and org-scoping.
- **An API key is not a user, and is deliberately weaker.** It carries a
  scope set rather than a role, and `require_scope` maps each scope onto the
  `Permission` a session would need for the same door — so opening a route to
  keys never widened what a person can do. It cannot reach the management
  surface that mints keys (nothing under `/api/v1/api-keys` accepts one; a key
  that could mint a key is a privilege-escalation primitive), and it cannot
  reach anything under `/api/v1/admin`. Its rate limit is its own
  (`api_key:{id}`), so a misbehaving integration cannot exhaust the budget of
  the people who deployed it. What it is *not* bounded by is a person's
  employment: deactivating a member does not revoke the keys they minted, which
  is correct — the key belongs to the organization — and is why
  [`docs/runbooks/api-keys.md`](../runbooks/api-keys.md) makes offboarding an
  explicit step rather than an assumption.
- **The marketing site's analytics surface is empty by default (T-308)**, and
  that is a security property as much as a privacy one: a third-party script is
  code from someone else's server executing on a first-party origin, changeable
  without a deploy. Adding one requires an entry in
  `apps/marketing/src/config/analytics.ts` with a written justification; that
  entry is simultaneously the build's subresource allowlist and the edge's CSP,
  so an undeclared host fails the build rather than shipping.

## 4. Out of scope for this document

- Anything inside a third-party provider's own infrastructure (Stripe,
  Resend, Google/GitHub, the object-storage provider) — see
  [`docs/runbooks/pentest-prep.md`](../runbooks/pentest-prep.md) §1 for
  the same boundary stated as pentest scope.
- Physical/hardware security of the hosting provider — delegated
  entirely, per V1.2.1's containerized-deployment model.

## 5. Keeping this current

Update this document in the same PR as any change that adds a new remote
service, a new inbound webhook, or moves a component across a trust
boundary (e.g. proxying file bytes through the backend instead of
presigned URLs). See [`sdlc.md`](sdlc.md) §1.
