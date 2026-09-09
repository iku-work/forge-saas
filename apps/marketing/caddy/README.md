# Generated Caddy site blocks (T-307)

`dev/marketing.caddy` and `prod/marketing.caddy` are **generated**, gitignored,
and rewritten by every `astro build` in this package. Do not edit them — edit
[`../scripts/caddy-site.mjs`](../scripts/caddy-site.mjs), which has the
reasoning for every line it writes.

Each directory is bind-mounted into the Caddy container at `/etc/caddy/conf.d`
(`docker-compose.yml` mounts `dev/`, `docker-compose.prod.yml` mounts `prod/`),
next to `dist/` at `/srv/marketing`. The repo-root `Caddyfile` and
`Caddyfile.prod` pull them in with:

```
import /etc/caddy/conf.d/*.caddy
```

Three things follow from that, and they are the whole design:

- **A glob that matches nothing is a warning, not an error.** So a checkout
  that has never built the frontend, and a product with
  `modules.marketing: false` (where `scripts/guard-enabled.mjs` deletes these
  files alongside `dist/`), both boot an edge that serves the app exactly as
  before and has no marketing host at all.
- **The block is only ever as current as the build.** Its
  `Content-Security-Policy` names each inline script by SHA-256 digest, so a
  hand-written copy is wrong the moment the code changes — and wrong here
  means a site whose JavaScript is silently blocked.
- **`caddy/*/*.caddy` is a turbo output** (`../turbo.json`). Without that, a
  cache hit would restore `dist/` and leave these files describing the build
  before it.

The `.gitkeep` files are committed because Docker needs the mount source to
exist in a fresh clone.

Regenerate without a full build (after, say, editing the generator):

```sh
node scripts/caddy-site.mjs        # reads ./dist
```
