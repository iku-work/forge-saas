# ADR-NNNN: <the decision, as a noun phrase>

<!--
Copy this file to docs/adr/NNNN-slug.md, taking the next free number.
Then run `make check-docs ARGS=--write` (or `python scripts/check_docs.py
--write` from backend/) to add the row to docs/adr/README.md's index.

One page. An ADR that takes more than half an hour to write is an ADR that
doesn't get written, and the one nobody wrote is the one the next maintainer
needed. Everything below is required -- `check_docs.py` fails the build on a
missing section -- but "Alternatives considered: none, X was the only option
that Y" is a complete answer when it is the true one.
-->

## Status

Proposed

<!--
The first non-empty line of this section is the status, and it is parsed:
it must start with `Proposed`, `Accepted`, or `Superseded by ADR-NNNN`
(pointing at an ADR that exists). Prose after the status word is allowed and
often earns its place -- ADR-0003's "Accepted -- plumbing only. No replica is
provisioned" says something a bare `Accepted` would actively mislead about.

An ADR is never edited into irrelevance and never deleted: a decision that
was true and is not any more is part of the record. Reverse one by writing
the ADR that replaces it and setting this one to `Superseded by ADR-NNNN`.
-->

**Date:** YYYY-MM-DD

<!-- The date the decision was made, not the date the file was last touched. -->

## Context

The forces, not the solution. What is true about the system, the constraints,
and the pressure that made this a decision rather than a default. A reader who
disagrees with the decision should still agree with this section.

Name the thing that is actually hard. ADR-0002's real subject is asyncpg's
prepared statements meeting transaction pooling, not "we added a pooler".

## Decision

What was decided, in the present tense and specific enough to check against
the code: settings and their values, file paths, the shape of the thing.

## Consequences

What is now true that wasn't, **including what got worse**. A consequences
section with no cost in it means the cost wasn't found yet, and it is the
half a future maintainer is reading this for: the capability they lost, the
second thing that can now be down, the two values that must stay in sync.

## Alternatives considered

Each one: what it was, and the specific reason it lost. "Simpler" and "we
know it better" are reasons; write them down as such. This is the section
that stops the same argument from being had again in eight months.
