# Adding a backend module (T-251)

The generator is `backend/scripts/new_module.py`, driven by `make new-module`.
It copies `backend/app/modules/_template/` — a complete, compilable reference
module built around one org-scoped entity called `Item` (T-250) — substituting
the module name everywhere and rendering the matching test file. Everything it
does is mechanical; everything it refuses to do is in the checklist it prints,
because each of those is a decision.

## Trigger

A new idea needs its first endpoint: a bounded piece of the product with its
own tables, its own service, and its own routes. Reach for this when you would
otherwise start a new directory under `backend/app/modules/` by hand.

Not this runbook: adding a route to a module that already exists (just add
it), or a UI-only feature with no new tables (start at step 8).

## Pre-checks

- A name in `snake_case`, ≤27 characters, that is not an existing module
  directory, a toggle in `ModuleName` (`backend/app/core/modules.py`), or a
  Python keyword. The generator refuses all of these before writing anything.
  Plural or singular is your call; the codebase uses both (`billing`,
  `notifications`).
- A clean-ish working tree. Generation touches only new paths, but you will be
  editing six shared files in the steps below, and a `git diff` you can read is
  worth more than it costs.
- `pip install -e ".[dev]"` done in `backend/` — the generator shells out to
  `ruff format`, and every verification step below needs the dev extras.

## Steps

### 1. Generate

```bash
make new-module NAME=invoices
```

Expected output — two `Generated ...` lines, then the eight-step checklist
with `file:line` pointers resolved against your tree as it is right now:

```
Generated backend/app/modules/invoices/ (7 files)
Generated backend/tests/modules/test_invoices.py

Not done for you, in the order they hurt least:

  1. Rename Item to Invoices
     ...
     -> backend/app/modules/invoices/models.py:25
```

A refusal prints one message and exits `2`, having written nothing:

```
$ make new-module NAME=Foo-Bar
'Foo-Bar' is not a valid module name.
Use snake_case: lowercase letters and digits, single underscores between
words, starting with a letter -- e.g. NAME=invoices or NAME=audit_log.
```

### 2. Confirm the starting point is green — before editing anything

```bash
cd backend
ruff check app/modules/invoices tests/modules/test_invoices.py
mypy --strict app/modules/invoices
pytest tests/modules/test_invoices.py
```

Expected: all three clean, five tests passing. This is the property CI's
"Module generator smoke" job protects, and it is worth seeing once with your
own eyes: from here on, anything red is something *you* did, which makes the
next hour much shorter.

The generated tests pass before the router is registered and before the table
exists because they assemble their own FastAPI app and create the table
themselves. Keep it that way as you edit — the three tests at the top
(cross-tenant isolation, the RBAC gate, cursor pagination) are §0.8
non-negotiables, not examples.

### 3. Rename `Item` to the real entity

Five places, all inside the new module: the class in `models.py`, its
`__tablename__` and index name, `ItemRepository`, `ItemService`, and the three
schemas. Table names here are singular `snake_case`.

The generated table is `invoices_item`, not `item`, so that two modules
generated before either is renamed cannot collide on `SQLModel.metadata` at
import time. Rename it to what it actually is (`invoice`) as part of this step.

Re-run step 2. Everything should still be green — the tests refer to the
module's public surface, not to `Item`.

### 4. Give the module its own permissions

`router.py` ships gated on `Permission.MEMBERS_READ` /
`Permission.SETTINGS_MANAGE` so the module is never open, not because those
are the right permissions. In `backend/app/modules/auth/permissions.py`:

```python
class Permission(StrEnum):
    INVOICES_READ = "invoices:read"
    INVOICES_MANAGE = "invoices:manage"
```

Grant them per role in `ROLE_PERMISSIONS`, then repoint `READ_PERMISSION` /
`MANAGE_PERMISSION` at the top of the module's `router.py`. Both are code — a
deploy, not a migration.

Expected: `pytest tests/modules/test_invoices.py` still green. The RBAC test
asserts a plain member can read and cannot write; if that flipped, the new
grants are wrong.

### 5. Register the router

`backend/app/api/v1/__init__.py`, in `build_router()`. Always-on module:

```python
router.include_router(invoices_router)
```

Optional module (one that a product built from this template can switch off):
put it behind `if modules.invoices.enabled:`, and add `invoices` to
`ModuleName` and `EnabledModules` in `backend/app/core/modules.py`, to
`MODULES` in `backend/product/config.py`, and to `modules` in the repo-root
`product.config.ts`. Both config files, or backend and frontend disagree about
what exists — see [module-toggle-guide.md](module-toggle-guide.md).

Expected: `curl -i localhost:8000/api/v1/invoices` returns `401`, not `404`.
A `404` means the router isn't registered; a `401` means it is and the gate is
doing its job.

### 6. Create the table

Add the import Alembic needs — autogenerate diffs only against what
`load_models_for_migration()` in `backend/alembic/env.py` imports by hand, so
without this it proposes **nothing**, silently:

```python
import app.modules.invoices.models  # noqa: PLC0415
```

Add the same import to `backend/app/models/__init__.py` (that is what the
running app uses). Then:

```bash
cd backend && alembic revision --autogenerate -m "invoices_tables" \
  --head "$(python -c 'from app.core.migrations import CORE_HEAD_REVISION; print(CORE_HEAD_REVISION)')" \
  --version-path alembic/versions/product
```

Both flags are required here, and neither is optional politeness:

- **`--head`** — every optional module's migrations sit on their own branch, so
  this repo always has five heads or more. Without it Alembic refuses with
  `Multiple heads are present`. `CORE_HEAD_REVISION` is the trunk tip
  (`app/core/migrations.py`), which is what a new module chains onto.
- **`--version-path`** — `alembic.ini` lists one `version_locations` entry per
  branch, and Alembic does not ask which one you meant: it silently writes to
  the *first*, `alembic/versions/core/`, i.e. into a template-owned directory
  that the next template update overwrites (`TEMPLATE.md`).

If the module is optional it also needs `--branch-label invoices` and its own
directory added to `version_locations`, so that disabling it can skip the
branch entirely.

Expected: one new script under `alembic/versions/product/` with a
`create_table` for your table and a `create_index` for the composite index.
**Read it before applying it** — autogenerate is a suggestion. Then
`make migrate`.

Anything the migration creates but the model does not declare — an extra
index, a `UniqueConstraint` beside an already-unique column — becomes a
permanent phantom diff: every later `--autogenerate` proposes dropping it, and
one of those scripts eventually gets applied without being read. Declare it in
`__table_args__` or don't create it. `alembic check` is the fast way to ask
whether the model and the database still agree.

If the module is optional, its table also belongs in `MODULE_TABLES`
(`alembic/env.py`) and its branch in `MODULE_BRANCHES`
(`app/core/migrations.py`), or turning the module off will leave its tables
behind.

### 7. Fill in the query budget

Every `list_*` route needs a row in `BUDGETS` in
`backend/tests/test_query_budgets.py`. This is not optional bookkeeping: the
suite fails the build on a list route without one, which is how N+1s stay
caught (T-243). Run `pytest tests/test_query_budgets.py` and use the count it
reports.

### 8. Regenerate the API client, then build the UI

```bash
make gen-client
```

The new endpoints appear in `@forge/api-client`, typed, for web and mobile.
No stack has to be running: since T-313 the schema is dumped offline by
`backend/scripts/dump_openapi.py`, from an app built with **every** module
enabled. That last part matters for a module that ships off — its routes are
in the generated client either way, and `useModules()` is what decides
whether the UI can reach them.
From there it is a feature directory under `apps/web/src/features/invoices/`
and TanStack Query:

```ts
const { data } = useQuery({
  queryKey: ["invoices", orgId],
  queryFn: () => invoicesApi.list({ cursor: undefined }),
});
```

### 9. Decide the real limit

`MAX_ACTIVE_ITEMS` in `service.py` is a plain constant, deliberately: a
brand-new module that 402s until someone configures a plan is worse than one
with a generous ceiling, and coupling the scaffold to billing would break it
for products that disable billing entirely. If this module is something you
sell by the seat, row or gigabyte, read the ceiling off the org's plan instead
— `app.modules.storage.service._check_quota` and
`app.modules.billing.dependencies.require_feature` are that shape end to end.

## Verification

The whole loop, from a clean checkout:

```bash
cd backend
ruff check . && ruff format --check .
mypy --strict app/ scripts/
pytest tests/ -q
cd .. && pnpm build
```

Plus the things only a running stack shows:

- `curl localhost:8000/api/v1/config` — if the module is optional, it appears
  in `modules` with the right value.
- `make gen-client`, then `grep invoices packages/api-client/openapi.json` —
  that document is what the client is generated from, and it is written with
  every module enabled. (`curl localhost:8000/openapi.json` answers the
  different question of whether *this* configuration serves the routes.)
- A cross-tenant probe against a real second org returns `404`, not `403` and
  not `200`. The generated test covers this against the module's own routes;
  it is worth confirming once through the real app, with real auth.

## Rollback

Before the migration has been applied anywhere but your machine, a generated
module is entirely deletable:

```bash
rm -rf backend/app/modules/invoices backend/tests/modules/test_invoices.py
git checkout -- backend/app/api/v1/__init__.py backend/app/models/__init__.py \
  backend/alembic/env.py backend/app/core/modules.py backend/product/config.py \
  backend/app/modules/auth/permissions.py backend/tests/test_query_budgets.py
rm backend/alembic/versions/<revision>_invoices_tables.py
```

After the migration has been applied: `alembic downgrade -1` first, then the
above. After it has shipped to production, treat it as any other table
removal — deprecate the routes, stop writing, then drop in a separate release.

If the generator itself half-wrote a module (a disk error, an interrupt), it
cleans up after itself; if you interrupted it hard enough that it couldn't,
the two paths in the `rm -rf` above are the only ones it ever creates.

## Escalation

- The generated module is red out of the box → the template drifted. That is a
  bug in `_template/`, not in your module: `backend/tests/test_module_template.py`
  and `backend/tests/test_new_module_script.py` are what should have caught it,
  and one of them is now wrong. Fix the template, not the generated copy.
- `alembic revision --autogenerate` proposes an empty migration → step 6's
  import is missing. It proposes dropping unrelated tables → you are running
  against a database migrated by a different module set; check `make
  migrate-plan`.
- Anything about the toggle mechanism itself →
  [module-toggle-guide.md](module-toggle-guide.md).
