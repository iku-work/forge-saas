# 03: Glossary copy sweep (Organization, Member)

**What to build:** every user-facing string says **Organization** for the tenant and
**Member** for a person in it. "Workspace" and "team" (as a synonym) disappear from the
product and marketing copy.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Read first

1. `GLOSSARY.md` (repo root, 15 lines).
2. `docs/runbooks/translations.md`, lines 1–70 only (how locales are edited).

## Steps

1. List every occurrence in English:
   ```bash
   grep -rniE '\b(workspace|workspaces|team|teams)\b' packages/i18n/locales/en/
   ```
   Expected (at time of writing): `common.json` `dashboard.empty_title`
   ("your workspace is ready"); `emails.json` (`member_invited`, a notification title
   "New team member", button "View team", trial body "your team's access");
   `marketing.json` (`hero.subtitle` "Accounts, teams, billing…",
   `highlights.accounts_title` "Accounts and teams", `pricing.feature_labels.seats`
   "{count, number} team members").

2. Rewrite each English value. Keep the key names. Suggested wording:
   - "Welcome — your workspace is ready" → "Welcome to {org}" only if the key already
     interpolates the org; otherwise "Your organization is ready".
     (Ticket 10 replaces this empty state anyway; just remove "workspace".)
   - "New team member" → "New member"
   - "View team" → "View members"
   - "your team's access" → "your organization's access"
   - "Accounts, teams, billing and an admin console…" → "Accounts, organizations, billing and an admin console…"
   - "Accounts and teams" → "Accounts and organizations"
   - "{count, number} team members" → "{count, number} members"

3. For each changed key, update the same key in `de`, `es`, `fr`, `fi`, `ar` **only if**
   the existing translation uses that locale's word for workspace/team in the same way.
   If you are not confident translating, delete the key from that locale's file instead
   (English fallback is allowed; `check-locales` only requires `en` complete). Never leave
   a translation that now contradicts the English.

4. Search code too (not just JSON): `grep -rniE '\bworkspace\b' apps/web/src packages/ui/src apps/marketing/src --include=*.tsx --include=*.ts --include=*.astro --include=*.mdx`.
   Only user-facing text matters. Blog/guide MDX content: change "workspace" where it
   means organization.

5. Rebuild: `pnpm --filter @forge/i18n build`.

## Verify

```bash
pnpm --filter @forge/i18n run lint
pnpm --filter @forge/i18n test
pnpm --filter web test
pnpm --filter marketing build && pnpm --filter marketing test
grep -rniE '\b(workspace|teams?)\b' packages/i18n/locales/en/   # should print nothing
```

Some web tests may assert the old English text (e.g. the dashboard empty title).
Update those assertions to the new text.

## Acceptance

- [ ] No "workspace" or "team(s)" in `packages/i18n/locales/en/`.
- [ ] Other locales either updated or the key removed (falls back to English).
- [ ] i18n lint, web tests, marketing build and tests pass.

## Don't

- Don't rename translation keys (only values), except where a test proves a key unused.
- Don't touch backend code identifiers (`org_id`, `tenancy`): this is copy only.
