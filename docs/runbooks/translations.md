# Translation workflow (T-232)

`packages/i18n/locales/en/*.json` is the single source of truth (see
`packages/i18n/README.md`). Every other locale is a translation of it. This
runbook covers the two ways translations get back into the repo: a TMS
(Crowdin or Lokalise) for an ongoing translation program, and a zero-dependency
manual CSV loop for everyone else. Both produce the same thing — updated
`packages/i18n/locales/<code>/*.json` files — so a project can start on the
manual loop and move to a TMS later without changing anything downstream.

## Source of truth and sync direction

The repo is always upstream for `en`. A TMS is upstream for everything else:

```
locales/en/*.json  --push-->  TMS  --translate-->  TMS  --pull-->  locales/<code>/*.json
```

Never edit a non-`en` locale file by hand while a TMS is wired up — the next
pull overwrites it. Either workflow below produces a normal PR; nothing
auto-merges.

## Manual CSV loop (no TMS, the default)

```bash
# Export every locale that's missing >0 keys, or one locale explicitly:
pnpm --filter @forge/i18n run i18n:export
pnpm --filter @forge/i18n run i18n:export -- --locale de
pnpm --filter @forge/i18n run i18n:export -- --locale de --missing-only

# Hand packages/i18n/i18n-export/de.csv to a translator. Columns: key, en
# value, current translation, namespace. Editing the `translation` column is
# the only thing a translator needs to touch.

# Bring it back:
pnpm --filter @forge/i18n run i18n:import packages/i18n/i18n-export/de.csv
```

Notes:

- `i18n:export` also writes a `.po` file alongside the `.csv` (gettext
  format) for translators whose tooling expects it — same data, either file
  works as input to a TMS that speaks `.po`.
- The target locale is inferred from the CSV's filename (`de.csv` → `de`),
  matching what `i18n:export` names its own output. Don't rename the file
  before re-importing.
- An empty `translation` cell means "leave this key alone" — it does not
  blank out an existing translation, and it does not create a new empty key.
  This is what makes exporting and immediately re-importing a no-op (CI
  checks this — see below).
- Importing a CSV with a key `i18n:import` doesn't recognize (typo, or a key
  that's since been removed from `en`) fails the whole import with a
  non-zero exit and the offending key's name; nothing is written.
- `i18n-export/` is gitignored — it's regenerated output, not checked in.

## Crowdin setup (reference)

Crowdin syncs against files, not individual keys, so point it at the `.po`
export rather than the JSON directly (Crowdin's JSON support assumes flat
key-value files; this package's files are nested).

`crowdin.yml` at the repo root:

```yaml
project_id: "<crowdin project id>"
api_token_env: CROWDIN_API_TOKEN
preserve_hierarchy: true
files:
  - source: /packages/i18n/i18n-export/en.po
    translation: /packages/i18n/i18n-export/%two_letters_code%.po
```

Sync direction:

1. `pnpm --filter @forge/i18n run i18n:export -- --locale en` to refresh the
   source `.po` (Crowdin's source language always tracks `en`).
2. `crowdin upload sources` — pushes the refreshed source strings.
3. Translators work in the Crowdin web UI.
4. `crowdin download` — writes back translated `.po` files under
   `i18n-export/<code>.po`.
5. `pnpm --filter @forge/i18n run i18n:import i18n-export/<code>.csv` for
   each downloaded locale (convert `.po` → `.csv` first if the team's Crowdin
   config only produces `.po`; the `.csv` importer is the one this package
   ships — a `.po`-native importer isn't implemented, this is the documented
   manual bridge).
6. Open a normal PR with the resulting `locales/<code>/*.json` diffs.

## Lokalise setup (reference)

Lokalise supports nested JSON natively, so it can sync directly against
`packages/i18n/locales/<code>/*.json` — the CSV/`.po` export exists for
translators and other TMS tooling, not as a hard dependency for Lokalise.

1. Create one Lokalise file per namespace (`auth`, `billing`, `common`, ...)
   or one project with `en` as the base language and nested-JSON format —
   either works, but keep it consistent with `packages/i18n/README.md`'s "one
   file per namespace" convention so the mapping stays obvious.
2. Upload each namespace on every `en` change:
   ```bash
   lokalise2 file upload --file packages/i18n/locales/en/<namespace>.json --lang-iso en
   ```
3. Translators work in the Lokalise web UI (or Lokalise's own MT + human
   review pipeline — equivalent to this package's `__review.json` markers,
   see `packages/i18n/README.md`).
4. Download translations straight into the locale directories:
   ```bash
   lokalise2 file download --format json --original-filenames true --directory-prefix packages/i18n/locales
   ```
5. Run `pnpm --filter @forge/i18n run check-locales` locally before opening
   the PR — catches orphan keys or a locale that regressed below the 95%
   coverage floor before CI does.

## CI checks (extend T-230's `check-locales`)

Both wired into `packages/i18n`'s `lint` script (`pnpm --filter @forge/i18n
run lint`), which CI's root `pnpm lint` step runs via Turborepo:

- **`check-locales`** (T-230): `en` is the only locale required at 100%
  coverage — a key that lands in another locale before `en` fails as an
  orphan key. Coverage/orphan/empty-value rules are unchanged by T-232.
- **`check-roundtrip`** (T-232): simulates `i18n:export --locale en`
  immediately followed by `i18n:import` and asserts every
  `locales/en/*.json` file comes back byte-identical. This is what "the
  export/import loop is lossless" means in practice — it would catch, for
  example, a change to the CSV/JSON merge logic that silently reorders keys
  or drops a translation with a comma in it.
