# __MODULE_NAME__

One-line description of what this module is for.

Generated from `backend/app/modules/_template` by `make new-module
NAME=__MODULE_NAME__`. Everything below is real, working code around one
example entity, `Item` — an org-scoped row with a name, an indexed `status`
filter column, and a soft delete.

## Public interface

- `app.modules.__MODULE_NAME__.service.ItemService` — create / list / get /
  update / soft-delete an `Item`, plus the plan-limit check. **The only entry
  point another module may use** (§0.1: cross-module access goes through
  services, never through a repository or a model).
- `app.modules.__MODULE_NAME__.router.router` — CRUD under
  `/api/v1/__MODULE_NAME__/`, gated on `READ_PERMISSION` / `MANAGE_PERMISSION`
  from the same file.
- `app.modules.__MODULE_NAME__.schemas` — `ItemCreateRequest`,
  `ItemUpdateRequest`, `ItemPublic`. The request/response contract; the
  generated TypeScript client is built from these via the OpenAPI schema.

`ItemRepository` and `Item` are internal. Import them from inside this module
only.

## Events emitted

Audit events, via `app.modules.audit.service.AuditService`:

| action                          | when                         |
| ------------------------------- | ---------------------------- |
| `__MODULE_NAME__.item_created`  | `ItemService.create_item`    |
| `__MODULE_NAME__.item_updated`  | `ItemService.update_item` (the `diff` carries only the changed fields) |
| `__MODULE_NAME__.item_deleted`  | `ItemService.delete_item`    |

No background jobs, no emails, no webhooks yet. If you add one, enqueue it by
name through `app.core.jobs` and list it here.

## After generating: the checklist

`make new-module` does the copying and the renaming. These are the things it
deliberately leaves to you, in the order they hurt least.

1. **Rename `Item` to `__ModelName__`** — the thing this module is actually
   about. It renames in five places: the class in `models.py`, its
   `__tablename__` and index name, `ItemRepository`, `ItemService`, and the
   three schemas. Table names in this codebase are singular `snake_case`.
2. **Give it its own permissions.** `router.py` starts with
   `READ_PERMISSION` / `MANAGE_PERMISSION` pointing at existing entries so
   the module is gated from its first request. Add this module's own pair
   (`<MODULE>_READ = "<module>:read"`, `<MODULE>_MANAGE = "<module>:manage"`)
   to `Permission` in `app/modules/auth/permissions.py`, grant them per role in
   `ROLE_PERMISSIONS`, and point the two constants at them. Both are code, so
   this is a deploy — not a migration.
3. **Register the router** in `backend/app/api/v1/__init__.py`'s
   `build_router()`. Add it to the always-on block, or behind an
   `if modules.<name>.enabled:` branch if the module is optional — in which
   case it also needs an entry in `ModuleName` / `EnabledModules`
   (`app/core/modules.py`) and in `backend/product/config.py` (T-059/T-060).
4. **Create the migration**: `alembic revision --autogenerate -m
   "__MODULE_NAME___tables"`, then read the generated script before applying
   it. Autogenerate diffs against the models Alembic imports *by hand* in
   `load_models_for_migration()` (`alembic/env.py`) — until this module's
   `models.py` is listed there it proposes nothing at all, silently. The
   running app needs the same import added to `app/models/__init__.py`. If
   the module is optional, the new table also belongs in `MODULE_TABLES` in
   `alembic/env.py` and in `MODULE_BRANCHES` in `app/core/migrations.py`, or
   disabling the module will not remove its tables.
5. **Fill in the query budget.** Every `list_*` route needs a row in
   `BUDGETS` in `backend/tests/test_query_budgets.py` — the suite fails the
   build on a list route without one, which is how N+1s stay caught (T-243).
6. **Decide the real limit.** `MAX_ACTIVE_ITEMS` in `service.py` is a plain
   constant. If this module is something you sell by the seat/row/gigabyte,
   read the ceiling off the org's plan instead — see
   `app.modules.storage.service._check_quota` and
   `app.modules.billing.dependencies.require_feature`.
7. **Regenerate the API client**: `make gen-client`. The new endpoints show up
   in `@forge/api-client` for web and mobile.
8. **Rename the tests too.** `backend/tests/modules/test___MODULE_NAME__.py`
   arrives with the three non-negotiables already written (cross-tenant
   isolation, the RBAC gate, cursor pagination). Keep all three green as the
   module grows; add cases below them.

## Layering, in one line

`router.py → service.py → repository.py → models.py`, imports flowing
downward only. `backend/.importlinter` enforces it in CI for every module
under `app.modules.*`, this one included.
