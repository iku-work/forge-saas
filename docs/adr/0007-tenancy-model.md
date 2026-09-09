# ADR-0007: Tenancy model — row-level, with RLS as the named next step

## Status

Accepted — **stay row-level.** No migration is planned, T-341 (schema-per-tenant
tooling) is closed as won't-do on the strength of this document, and the
thresholds in [Revisit triggers](#revisit-triggers) are the only thing that
should reopen it.

**Date:** 2026-08-31

## Context

Every multi-tenant product is asked this eventually, usually by someone who has
just read a post about it: *should each tenant get its own Postgres schema?*
The question keeps coming back because it is asked in the abstract, where
schema-per-tenant sounds obviously safer, and answered in the abstract, where
"we use `org_id` everywhere" sounds obviously simpler. Neither is an argument.

This template is row-level today. Every tenant-owned table carries `org_id`,
`BaseRepository` (`app/core/repository.py`) applies `org_id == self.org_id` to
everything it builds, and §0.2 makes that a rule with a test behind it —
`tests/security/test_tenant_isolation.py` probes across the boundary on every
list endpoint, and T-311 added a case that crosses it holding an API key
rather than a session.

Three things make the question worth answering *now* rather than when it is
urgent:

1. **The performance motivations have already been addressed by other means.**
   Read replicas (T-246, plumbing only), query budgets that fail the build on
   an N+1 (T-243), a Redis cache layer with an invalidation matrix (T-240) and
   pgbouncer in transaction mode (T-244) between them cover the load a
   schema split is usually reached for. Someone proposing schemas for
   performance is proposing the second answer to a question the first four
   already answered.
2. **The isolation motivation is real and is not the same question.** "Prove
   to an auditor that tenant A's rows cannot be read while serving tenant B"
   is a different requirement from "make queries faster", and the option that
   answers it is not schema-per-tenant.
3. **The cost of schema-per-tenant lands on a decision this template has
   already made.** T-244 put pgbouncer in *transaction* mode, and a
   session-level `SET search_path` does not survive transaction pooling — see
   [Consequences](#consequences), because that interaction is not a detail, it
   is a cross-tenant data leak with a plausible-looking implementation.

## Decision

### 1. The tenancy model stays row-level

`org_id` on every tenant-owned table, filtered in one place
(`BaseRepository`), enforced by §0.2 and probed by T-049's sweep. Nothing
changes.

### 2. The middle option is named, and it is PostgreSQL row-level security

The framing "row-level vs schema-per-tenant" is a false pair, and naming the
third option is the most useful thing this document does. **PostgreSQL RLS**
attaches a policy to a table so the *database* refuses rows outside the
current tenant, whatever the query says:

```sql
ALTER TABLE stored_file ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON stored_file
  USING (org_id = current_setting('app.current_org')::uuid);
```

That gets most of what schema-per-tenant is reached for — a guarantee that
does not depend on every future query remembering `org_id` — for a fraction of
the operational cost, and it *composes* with the current model instead of
replacing it. It has the same pooling problem as `search_path` (see below) and
the same fix, but it is one `SET LOCAL` in one place rather than a schema
router threaded through everything.

**RLS is not adopted today**, because the isolation assurance it buys is not
currently a requirement anyone has stated; it is the first thing to reach for
when one does.

### 3. Schema-per-tenant is rejected, on numbers

| Criterion | Where the number comes from | Threshold that would change this |
|---|---|---|
| Largest tenant's share of the biggest table | `SELECT org_id, count(*) FROM webhook_delivery GROUP BY 1 ORDER BY 2 DESC LIMIT 5` | one tenant > 40% of the largest table |
| Noisy-neighbour incidents, last quarter | incident log; `pg_stat_statements` (T-243's `slow-queries.md`) | ≥ 2 attributable to one tenant's load |
| Contractual demand for physical isolation | signed contract, not a questionnaire | any, once signed |
| Per-tenant point-in-time restore | a written requirement, not a preference | any, once written |
| Tenant count and growth | `SELECT count(*) FROM organization` | see the degradation note below |

None of these hold for this template as it ships (one seeded organization and
no customers), which is exactly why the answer is a default rather than a
measurement — and why the triggers below are written as thresholds a
dashboard can show rather than as opinions.

### 4. If it is ever reached for, these are the costs to have already accepted

- **Migration time becomes O(tenants).** Every `ALTER TABLE` runs once per
  schema. At 1,000 tenants a one-second migration is a seventeen-minute
  deploy; at 5,000 it is an outage window. Postgres itself degrades past
  roughly 1–5k schemas — `pg_class` and `pg_attribute` grow with
  schemas × tables, autovacuum has more to do, and `search_path` resolution
  and query planning get measurably slower.
- **Per-tenant restore becomes easy**, which is the one genuine win: `pg_dump
  --schema=tenant_123` is a real answer to "restore this customer to
  yesterday" that row-level does not have. It is worth naming honestly,
  because it is the argument this decision is weakest against.
- **Every schema change becomes a scheduled operation** with a runner,
  progress, resume and a rollback plan — which is why T-341 exists as a task
  at all, and why closing it is a real saving rather than a deferral.

## Consequences

**What stays true.** One migration path, one `alembic upgrade`, one connection
pool, one set of query plans. `BaseRepository` remains the single place tenant
scoping is expressed, which is what makes §0.2 checkable at all.

**What we are choosing not to have.** Physical isolation, and cheap per-tenant
restore. A cross-tenant leak in this model is a missing `WHERE` clause, not an
impossible state — the mitigation is a code rule plus a test sweep, and that
is a weaker guarantee than a schema boundary. This document's job is to make
that a decision rather than an oversight.

**The pooling interaction, stated before anybody writes the middleware.**
T-244 put pgbouncer in transaction mode, which returns a connection to the
pool at the end of every transaction. A session-level `SET search_path` (or
`SET app.current_org`, for RLS) therefore leaks into whichever tenant's request
gets that connection next — a cross-tenant data leak produced by a perfectly
reasonable-looking implementation. Three options, and the choice must be made
*before* any of this is built:

1. **`SET LOCAL` inside every transaction.** Scoped to the transaction, so it
   dies with it. Correct under transaction pooling, and the only option here
   that does not forfeit T-244. Requires that every tenant-scoped read is
   inside a transaction — which `get_session` already guarantees.
2. **A schema-qualified query layer.** No session state at all; every table
   reference is `tenant_123.stored_file`. Correct, and it means rewriting
   every query in the product.
3. **Session pooling for these connections.** Works, and gives back what T-244
   bought — ADR-0002 exists because transaction pooling is what makes this
   product's connection count survive its worker count.

**This ADR chooses option 1 in advance**, for RLS and for schema-per-tenant
alike, so that T-341 (if it is ever reopened) starts from a decision rather
than from a design meeting. The concurrency test T-341 asks for — one that
fails without it — is the acceptance criterion for that choice.

## Alternatives considered

**Schema-per-tenant.** Rejected on the numbers in Decision 3: not one of the
five criteria holds, the performance motivation is already addressed by
T-240/243/244/246, and the isolation motivation is better served by RLS at a
fraction of the cost. Revisit only on a trigger below.

**Database-per-tenant.** Everything schema-per-tenant costs, multiplied: a
connection pool per tenant (which pgbouncer's whole purpose is to avoid), a
migration run per tenant against a separate server, and cross-tenant admin
queries — T-141's directory, T-143's audit feed — becoming a fan-out instead
of a query. Appropriate for a handful of very large, contractually isolated
customers; not a default for a template.

**Adopt RLS now, pre-emptively.** Tempting, and rejected on the same principle
as ADR-0003's "plumbing only, no replica provisioned": RLS is not free — every
policy is a predicate on every query, `SET LOCAL` has to be threaded through
`get_session`, and a policy that is subtly wrong fails *closed* in production
and silently *open* in a test that forgot to set the GUC. Adopting it before
anyone needs it means paying that and getting an assurance nobody has asked
for. Decision 2 names it so the next person does not have to rediscover it.

**Leave the question open.** The status quo before this ADR, and the reason it
exists: an unanswered architecture question gets re-argued in every design
review at whatever depth the loudest participant has energy for. Writing down
"row-level, here are the five numbers that would change it" costs one page and
ends that.

## Revisit triggers

Each is a threshold something can show, not a feeling:

1. **One tenant exceeds 40% of the rows in the largest table.**
   `SELECT org_id, count(*) FROM webhook_delivery GROUP BY 1 ORDER BY 2 DESC`
   — swap in whichever table is largest at the time.
2. **Two or more noisy-neighbour incidents in a quarter** attributable to one
   tenant, from the incident log plus `pg_stat_statements`
   (`docs/runbooks/slow-queries.md`).
3. **A signed contract requiring physical isolation.** Signed, not asked about
   in a security questionnaire — the questionnaire answer is this document.
4. **A written per-tenant point-in-time restore requirement.** This is the one
   row-level genuinely cannot serve well, so it is the trigger most likely to
   fire on its own merits.
5. **Tenant count crosses 1,000** — not because row-level stops working, but
   because past that point the *cost of switching* rises fast enough that the
   decision should be re-made deliberately rather than inherited.

Triggers 1 and 2 point at RLS *or* at more of what T-240/243/246 already
provide. Only triggers 3 and 4 point at schemas.
