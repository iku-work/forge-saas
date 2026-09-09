# Module config fixtures (T-291)

Each `<name>.py` here is a complete replacement for `backend/product/config.py`,
paired with a `<name>.config.ts` twin that replaces the repo-root
`product.config.ts`. CI's `module-matrix` job copies a pair over the real files,
runs a whole combination (migrate from empty, boot, `/api/v1/config`, pytest,
`pnpm build && lint && type-check`), and restores both files afterwards.

They are fixtures for a job, not for the in-process suite —
`backend/tests/modules/test_module_toggles.py` monkeypatches `MODULES` instead
and never touches these files. The one thing that reads them from inside pytest
is `tests/test_product_config_parity.py`, which guards their shape.

| Fixture | Combination | What it is |
|---|---|---|
| *(none)* | **A** | `backend/product/config.py` exactly as committed — see below |
| `minimal` | **B** | every optional module `False`: the minimum-surface product |
| `phase4` | **C** | the three Phase 4 modules on, everything else at its default |

**Combination A deliberately has no fixture.** This is a template: a product
edits `backend/product/config.py`, and that edited file is what combination A
must prove boots and builds. A committed "default" fixture would test the
template's opinion instead of the product's, and would fail the moment anyone
used the template as intended.

## Rules these files must follow

`tests/test_product_config_parity.py` enforces all three:

1. Each fixture imports and passes `validate_modules()` — a fixture that
   violates a dependency edge would fail the matrix job at boot, several
   minutes in, instead of here in milliseconds.
2. Each fixture agrees key-for-key **and value-for-value** with its
   `.config.ts` twin, the same mirror rule the shipped pair lives under.
3. Everything that is *not* `MODULES` (branding, support, locales) is a
   verbatim copy of the committed `backend/product/config.py`, and the same
   for the `.config.ts` twins. That is what makes the duplication safe: add a
   setting to the real config and this fails immediately, rather than the
   matrix job failing later on an app that refuses to boot.
