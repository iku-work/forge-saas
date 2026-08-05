# Translation workflow (T-232)

`packages/i18n/locales/en/*.json` is the single source of truth (see
`packages/i18n/README.md`). Every other locale is a translation of it. This
runbook covers the two ways translations get back into the repo: a TMS
(Crowdin or Lokalise) for an ongoing translation program, and a zero-dependency
manual CSV loop for everyone else. Both produce the same thing — updated
`packages/i18n/locales/<code>/*.json` files — so a project can start on the
manual loop and move to a TMS later without changing anything downstream.

## Trigger

- New or changed `en` strings need translating — the usual case, and the one
  `--missing-only` is for.
- A translator or TMS has produced translations that need to land in the repo.
- A locale has fallen below the 95% coverage floor and `check-locales` is
  failing the build.

Not this runbook: adding a *new* locale (that is `SUPPORTED_LOCALES` plus
routing — `packages/i18n/README.md` and T-233), or editing `en` itself, which
is a normal code change like any other string in the app.

## Pre-checks

- **Know which workflow this project is on.** Manual CSV and a TMS are
  mutually exclusive in practice: with a TMS wired up, a hand-edited non-`en`
  file is overwritten by the next pull, silently.
- `en` is complete and merged first. It is the source; a key that exists in
  another locale before it exists in `en` fails `check-locales` as an orphan,
  which is the check working.
- `pnpm install` done, and `@forge/i18n` built if you are running its scripts
  across packages — a stale `dist` is the standard way this looks broken when
  it isn't.
- Nothing uncommitted in `packages/i18n/locales/`. Import writes over those
  files; the diff afterwards is what you review, and it is only readable if it
  started clean.

## Source of truth and sync direction

The repo is always upstream for `en`. A TMS is upstream for everything else:

```
locales/en/*.json  --push-->  TMS  --translate-->  TMS  --pull-->  locales/<code>/*.json
```

Never edit a non-`en` locale file by hand while a TMS is wired up — the next
pull overwrites it. Either workflow below produces a normal PR; nothing
auto-merges.

## Steps

Three workflows, one at a time — pick the one this project is on.

### Manual CSV loop (no TMS, the default)

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

### Crowdin setup (reference)

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

### Lokalise setup (reference)

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

## Verification

Before opening the PR:

```bash
pnpm --filter @forge/i18n run lint     # check-locales + check-roundtrip
git diff --stat packages/i18n/locales  # only the locale you imported moved
```

Then look at the app in that locale. The checks below prove the files are
structurally sound; they say nothing about whether a string fits. Two things
only a human catches: text that overflows its control (German is reliably
longer than English), and an interpolation that survived the round trip
syntactically but lost its meaning — `{{count}}` in the wrong clause is a
valid ICU message and a wrong sentence.

The negative check: an import that reports success but produces an empty
`git diff` did nothing. Usually an all-empty `translation` column, which is
defined to mean "leave this key alone".

### CI checks (extend T-230's `check-locales`)

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

## Rollback

Locale files are ordinary tracked JSON, so a bad import is `git checkout --
packages/i18n/locales/<code>/` before commit, or a revert after. Nothing else
in the repo holds translation state — `i18n-export/` is gitignored, generated
output.

A TMS is the exception: it keeps its own copy, and reverting the repo does not
un-translate anything upstream. The next pull will happily re-introduce
whatever was reverted. Fix it in the TMS first, then pull again — reverting on
this side alone is a loop, not a rollback.

## Escalation

- **`check-locales` fails on a locale nobody touched.** A key was added to
  `en` without a translation, which is the expected state until the next
  translation round — it fails the build once coverage crosses the 95% floor.
  Either translate it or accept the floor is doing its job; do not lower it to
  get a PR through.
- **`check-roundtrip` fails.** This is not a translation problem: the
  export/import pipeline itself lost or reordered something. Do not import
  anything else until it is fixed, and keep the failing CSV — it is the
  reproduction.
- **An import fails on an unrecognized key** and the key looks correct: `en`
  has moved since the CSV was exported. Re-export, hand the translator the
  fresh file, and don't reconcile by hand — nothing was written, so there is
  no half-applied state to clean up.
- **A translation renders as the raw key in the app.** Namespace mismatch, not
  a missing translation. `packages/i18n/README.md`'s one-file-per-namespace
  convention is the thing that broke.
