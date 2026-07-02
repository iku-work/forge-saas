# ForgeSaaS Template Contract

This file is the contract every product forked from this template must read
before customising anything. It defines which files the template owns,
which files the product owns, and how to pull template updates without
fighting your own customisations.

## Files the template owns (never edit in a product)

Every file not listed under "Files the product owns" below. Template
updates will overwrite them. Resolve merge conflicts by taking the template
version unless you have a documented reason not to (note the reason in
`CHANGELOG.product.md`).

## Files the product owns (template never touches)

- `product.config.py` — module toggles + branding (backend)
- `product.config.ts` — module toggles + branding (frontend)
- `.env.product.example` — product-specific env vars
- `apps/web/src/routes/_product/` — product-specific routes
- `backend/app/modules/<product-module>/` — product-specific modules (assign yourself a migration prefix block below)
- `CHANGELOG.product.md` — product-level changelog
- `README.md` — overwrite freely

## Migration prefix convention

Alembic migrations are numbered so template and product migrations never
collide:

| Range       | Owner                                  |
|-------------|-----------------------------------------|
| 0001 – 0099 | Template (core + optional modules)      |
| 0100 – 0199 | First product to fork — assign yourself a range below |

Products: assign yourself a prefix block and document it here when you fork:

| Product      | Prefix range |
|--------------|--------------|
| (your name)  | 0100 – 0199  |

## Pulling template updates

```sh
git fetch upstream
git merge upstream/vX.Y.Z     # always merge a tag, never upstream/main directly
# Resolve conflicts; take the template version unless product.config.py says otherwise
make dev && make test          # verify before pushing
```

`make update-from-template` and `make check-template-drift` (see `Makefile`)
automate the fetch/merge and the "are we behind" check respectively.

## Setting up the upstream remote

When you fork this repo to start a product, add the template as a second
remote so you can keep pulling updates:

```sh
git remote add upstream <forgesaas-template-repo-url>
git fetch upstream
```
