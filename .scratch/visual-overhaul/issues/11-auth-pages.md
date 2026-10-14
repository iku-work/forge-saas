# 11: Card-less sign-in and account pages

**What to build:** sign-in and every other account page (register, forgot password,
reset password, verify email, magic link, accept invitation, confirm/cancel email
change, two-factor challenge) drop the card. Each is a narrow form on the quiet page:
logo, condensed title, one-line description, fields, one full-width primary button,
then alternatives, then the link to the other flow.

**Blocked by:** 01.

**Status:** done

## Read first

1. `apps/web/src/features/auth/AuthCard.tsx` (the shared wrapper; most of the change is here).
2. `apps/web/src/features/auth/LoginPage.tsx` (the most complex page).
3. Prototype CSS: `.auth`, `.auth-body`, `.brand`, `.auth h1`, `.field`, `.or`, `.stack`,
   `.auth-alt`, `.auth-legal`. Look at the prototype's "Sign in" screen.

## Design

- Page: `min-h-dvh flex flex-col p-6`, `bg-background`. Body column `m-auto w-full max-w-[22.5rem] py-12`
  (**keep margin-based centring**; it is the existing fix that keeps the top of a tall form reachable).
- Brand row: 1.75rem `bg-primary text-primary-foreground rounded-md` monogram + product
  name `font-semibold`, `mb-10`. Product name comes from wherever `AuthCard` gets it today.
- Title: `text-[2.125rem] leading-[1.1] font-semibold [font-stretch:82%] tracking-[-0.025em]`.
  Description: `mt-1.5 mb-8 text-foreground-secondary`.
- Fields: label above input, `gap-1.5`, `mb-4`; inputs `h-10`. On login, "Forgot password?"
  sits at the end of the password label row as a link.
- Primary button: full width, `h-10`.
- Divider "or": a word between two hairlines, `my-6 text-xs text-muted-foreground`.
- Alternatives (OAuth providers, SSO, "Email me a sign-in link"): full-width secondary
  buttons stacked with `gap-2`; the magic-link option as a ghost button. Only those enabled
  today (the existing tests cover OAuth enabled/partial/disabled/SSO; keep that logic).
- Switch-flow link ("New to X? Create an account"): `mt-8 text-[0.84rem] text-foreground-secondary`.
- Footer at the page bottom: Terms and Privacy links if they exist today, small and muted.

## Steps

1. Rewrite `AuthCard` (consider renaming the component to `AuthLayout`; if you rename,
   update every import; keep the old export name as an alias if that is less churn)
   to render the design above. Keep its props so pages don't all change.
2. Go through each auth page and fix anything that relied on the card (headers, footers,
   spacing). One page at a time; run its tests after each.
3. `TwoFactorChallenge.tsx`: same layout; the code input keeps `autocomplete="one-time-code"`.
4. Keep every `data-testid` (`login-email-input`, `login-password-input`, `login-submit`, …):
   the E2E suites rely on them.

## Verify

```bash
pnpm --filter web exec vitest run src/features/auth
pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web test
```

Manual: all auth routes in light/dark, `ar`, at 375px wide (no horizontal scroll, the
form fits), and a short window (form taller than the screen still scrolls to its top).

## Acceptance

- [x] No card on any auth page; layout matches the design.
- [x] OAuth/SSO/magic-link options still appear only when enabled.
- [x] All auth tests and `a11y.test.tsx` pass; test ids unchanged.
- [x] Lint and type-check clean.

## Notes

Completed 2026-10-06; see `PROGRESS.md`. Renamed to `AuthLayout` with no alias. The magic
link is now an alternative under "or" rather than a footer link.
