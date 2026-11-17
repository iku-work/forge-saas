# ADR-0010: Building blocks live outside the template and are copied into a product

## Status

Accepted -- the split and the shape of a block. No block exists yet: the
`forge-blocks` repository and `make add-block` are built with the first real
one, extracted from a product, not ahead of it.

**Date:** 2026-11-17

## Context

Products built from this template will build capabilities that a later
product could use again: document scanning, PDF export, a CSV importer,
e-signatures. The template already has a reuse mechanism, optional modules
that a product switches on and pulls with `make update-from-template`, and
the easy move is to promote every such capability into it.

That would make the template carry every product's ideas. Worse than the code
is what these capabilities bring with them. PDF rendering needs font and
layout libraries in the backend image. Scanning needs an OCR engine, or a paid
API with its own key, and long-running jobs. A module switched off still ships
its dependencies, its image layers and its CVE alerts to every product, and
every block adds a leg to the module matrix that every template PR pays for.

So there are two kinds of reuse here, and they need different homes:

- **Platform:** what every SaaS needs and what the template exists for:
  sign-in, organisations, billing, admin, files, notifications.
- **Capabilities:** what some products need: features a customer buys,
  built on top of the platform.

What is hard is the distribution. A capability is rarely a library. It is a
table and its migration, a route, a background job, a web page, translations,
GDPR purge rules and a module toggle, in two languages. The backend installs
from a hash-pinned lock (`pip install --require-hashes`), which refuses a
dependency installed straight from git.

## Decision

**The template holds the platform only.** A capability that some products
need goes to a separate repository, `forge-blocks`, as a *building block*.
The template's optional modules stay what they are: platform features a
product may not want (billing, SSO, webhooks), not a catalogue of
capabilities.

**A block has two layers and a manifest:**

```
forge-blocks/blocks/<name>/
├── core/        # plain Python: no FastAPI, no database, no template imports
├── forge/       # the adapter: module (models, service, router, job),
│                #   its own alembic branch, its own i18n namespace
├── web/         # React components and hooks
├── block.toml   # pip and npm dependencies, apt packages for the image,
│                #   settings with defaults, secret names, module toggle
└── tests/
```

`core/` does the work and depends on nothing of ours. `forge/` wires it in
through what the template already provides: storage, ARQ jobs, the virus
scan, org scoping, module toggles, `product/config.py` for settings.

**A product gets a block by copying it in**, as shadcn/ui does with
components: `make add-block NAME=<name> VERSION=<tag>` copies `forge/` and `core/` into
`backend/app/modules/<name>/` and `web/` into `apps/web/src/features/<name>/`,
applies or prints the `block.toml` checklist, and records the
source and version in `blocks.lock`. From then on the product owns that code.
Updating is running the command again with a newer tag and reviewing the diff.

**A block is born in a product.** It is built there in this shape from the
first commit, and extracted into `forge-blocks` once it works.

## Consequences

- The template stays the size of the platform. A product that never exports a
  PDF never carries a PDF renderer, its image layers or its advisories.
- A product can change its copy of a block freely, with nothing to fork and
  no version to pin.
- **Updates do not flow by themselves.** A fix to a block reaches a product
  only when someone re-runs `make add-block` and merges the diff, and a
  product that edited its copy merges by hand. This is the price of owning
  the code. `blocks.lock` is what says which products are behind.
- A block can break when the template changes under it. Keeping `forge/` thin
  limits that, and so does putting the logic in `core/`, which has no
  template imports. `forge-blocks` CI needs a template checkout to test the
  adapters against.
- There is now a second repository to keep, with its own CI and tags.
- Nothing enforces the shape until the first block exists. This ADR is the
  checklist until `make add-block` reads `block.toml`.

## Alternatives considered

- **Every reusable capability as an optional template module.** Lost on the
  context above: each product pays for the others' dependencies, and the
  template stops being a platform. It remains right for platform features.
- **Published packages** (a private PyPI index and npm registry, one package
  per block). Updates would be a version bump, but the backend's hash-pinned
  lock rules out git installs, so every block needs built wheels with
  published hashes plus an npm package: two release streams per block. A
  package also cannot carry migrations, translations and admin wiring the way
  a module folder does. Worth revisiting once many products share many blocks
  and update them often.
- **Git submodules or subtrees.** Lost on daily friction: a submodule pins a
  commit the product cannot edit in place, and a subtree merges history into
  every product. Copy-in gives the same result with ordinary files.
- **No shared home: copy between products by hand.** This is what happens
  without a decision. It loses track of where a block came from and which
  copy has the fixes, which is the job `blocks.lock` does.
