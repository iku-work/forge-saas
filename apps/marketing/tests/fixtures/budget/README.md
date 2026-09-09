# `check-budget` fixtures

`clean/` is a build with nothing wrong with it: a self-hosted `@font-face`
carrying `font-display: swap`, a `<link rel="preload" as="font">` for it, an
`<img>` with both dimensions, and no subresource from another origin. It also
carries the two things the gate must *not* report — a `rel="canonical"` and an
`hreflang` cluster naming a different origin, which are statements about URLs
rather than requests for them.

`tests/budget.test.ts` copies it to a temp directory and rewrites one file per
case, rather than committing a directory per case: one visible mutation beats
a set of near-identical directories a reader has to diff. This is the same
arrangement `tests/fixtures/indexing/` uses, and for the same reason.
