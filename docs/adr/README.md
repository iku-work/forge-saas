# Architecture Decision Records

A record of decisions that were expensive to make, so they don't have to be
made twice — and so the next person to look at a surprising piece of this
codebase finds the reasoning instead of reconstructing it.

## What deserves an ADR

Two tests. If either one is yes, write one:

1. **Reversing it would be expensive.** It touches a data model, a wire
   format, a deployment topology, a vendor, or a security boundary — anything
   where "we'll change it later" means a migration, a rewrite, or a
   coordinated deploy.
2. **A future maintainer will ask "why on earth".** The choice looks wrong,
   or arbitrary, or needlessly complicated from the outside, and the reason it
   isn't lives in a constraint that is not visible in the code. ADR-0002's
   three prepared-statement driver arguments are unreadable without the ADR;
   with it they are obvious.

What does **not** deserve one: choices with an obvious default and no cost to
changing (a library for date formatting, a directory name), anything already
enforced and explained by a test, and anything whose whole content is
"we did the normal thing". A repo full of ceremonial ADRs is a repo where
nobody reads the one that mattered.

Where it goes if not here: an implementation detail belongs in a docstring or
a comment next to the code, an operational procedure belongs in
[`docs/runbooks/`](../runbooks/), and a security claim belongs in
[`docs/security/`](../security/) with the evidence next to it.

## Writing one

1. Copy [`_template.md`](_template.md) to `NNNN-slug.md`, taking the next free
   number. Numbers are permanent and never reused.
2. Fill in every section. One page — an ADR that takes more than half an hour
   to write is one that doesn't get written.
3. Regenerate the index below:

   ```bash
   make check-docs ARGS=--write
   ```

4. Commit the ADR **in the same PR as the change it describes**. An ADR
   written afterwards is a summary; one written with the code is a decision.

## Lifecycle

`Proposed` → `Accepted` → `Superseded by ADR-NNNN`.

An ADR is never rewritten into agreement with a later decision, and never
deleted. A decision that was true and is not any more is the most useful kind
of record there is: it tells you what was already tried. To reverse one, write
the ADR that replaces it and set the old one's status to
`Superseded by ADR-NNNN` — the checker verifies the ADR it points at exists.

`Proposed` is also the honest status for a decision that has been *scoped* but
not made, where the constraints are visible now and won't be later — see
[ADR-0004](0004-marketing-framework.md), which records what will decide the
marketing framework without picking it.

## What CI checks

`backend/scripts/check_docs.py`, run by the `lint` job (`make check-docs`
locally), holds the mechanical half:

- every ADR carries a parseable status line and a `**Date:**`;
- a `Superseded by ADR-NNNN` points at a file that exists;
- every ADR has its Status/Context/Decision/Consequences/Alternatives
  sections, in that order;
- the index below matches the files on disk exactly — number, title, status
  and date all come from the ADRs themselves, so the table cannot drift.

It does not check whether any of the prose is true. That is what the "same PR
as the change" rule in step 4 is for.

## Index

<!-- adr-index:start -->
| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-security-baseline.md) | Security Baseline — OWASP ASVS 4.0 Level 2 | Accepted | 2026-07-08 |
| [0002](0002-pgbouncer.md) | PgBouncer in transaction mode | Accepted | 2026-08-04 |
| [0003](0003-read-replica.md) | Read-replica routing | Accepted | 2026-08-05 |
| [0004](0004-marketing-framework.md) | Marketing site framework | Proposed | 2026-08-05 |
<!-- adr-index:end -->
