# <Operation> runbook (T-NNN)

<!--
Copy this file to docs/runbooks/<operation>.md.

A runbook is read by someone who is tired, or paged, or doing this for the
first time -- often all three. The six headings below are the contract, in
this order, and `check_docs.py` fails the build if one is missing. Extra
sections are fine anywhere; these six have to be there.

A document in docs/runbooks/ that is not a procedure -- an inventory, a
reference, the record of a verification pass that already happened -- opts
out by carrying, in its first few lines:

    <!- - runbook-template-exempt: <reason> - ->

The reason is required and is read by a human, not a script. "Not a
procedure" is not one; "record of the T-160 verification pass, not a
procedure to run again" is.
-->

One or two sentences on what this operation is and where the policy behind it
lives (`docs/security/*`, an ADR), so the reader knows what this document is
before the first heading.

## Trigger

When to reach for this — the observable condition, not the abstract topic: an
alert firing by name, a specific failure message, a scheduled cadence, a
support case with a particular shape.

Say what is *not* this runbook too, and where that goes instead. The wrong
runbook confidently followed is worse than no runbook.

## Pre-checks

What must be true before step 1, each one checkable and each one there
because skipping it hurts. Access and credentials. A recent backup, if the
operation can lose data. State this operation assumes and does not create —
the half-finished previous run, the migration that has to be applied first.

## Steps

Numbered, one action per step, in the order they are performed.

### 1. <Action>

The exact command, copy-pasteable, with the placeholders obvious:

```bash
make something NAME=<the thing>
```

**Expected:** what the terminal actually prints when this works — a line of
real output, an exit code, a status transition. Every step gets one. "Expected
output" is what turns a step from an instruction into a checkpoint, and it is
the only thing that tells an operator at 3am whether to continue or stop.

Anything surprising about the step goes right here, in the step, not in a
notes section at the bottom that nobody reaches.

### 2. <Action>

…

## Verification

How to know the operation actually worked, from outside it — not "step 4 said
OK" but a fresh query, a health endpoint, a metric, a login. Include the
negative check where one exists: the thing that would still look fine if the
operation had silently done nothing.

## Rollback

How to undo it, and — before that — whether it can be undone at all. If there
is a point of no return, name the step it is, so the reader meets it before
they cross it rather than after. If nothing needs undoing, say why in a
sentence: "nothing to roll back — the breaker closes itself on the next
successful call" is a complete Rollback section.

## Escalation

What to do when this runbook doesn't work: the failure modes that mean stop,
who or what to reach for (a vendor status page, the on-call for another
system, the ADR that explains the design), and what to capture before
restarting so the next attempt isn't blind.
