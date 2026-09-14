/**
 * T-307: the site block that serves this app, and the claims it makes.
 *
 * Two kinds of test here, because there are two ways this can be wrong.
 *
 * The first is that the block *says* something untrue about the build it was
 * written from -- a CSP whose hashes match no script on the site, a locale
 * that has no pages, a cache rule for a directory Astro stopped writing. Those
 * are checked against a real `dist/`, recomputing the answer from the built
 * files rather than restating what the generator did.
 *
 * The second is that the block is not valid Caddy configuration at all, which
 * no amount of string matching can tell you. That is what the `caddy validate`
 * cases at the bottom are for: the real `Caddyfile`, the real generated block,
 * and the actual binary that has to load them. They are skipped, loudly, on a
 * machine with no Docker.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { afterAll, describe, expect, it } from "vitest";

import {
  CADDY_DIR,
  SITE_ROOT,
  TARGETS,
  inlineScriptHashes,
  renderSite,
} from "../scripts/caddy-site.mjs";
import { PRODUCTION_ORIGIN } from "../src/config/indexing";
import {
  APP_ROOT,
  DIST,
  MINIMAL_FIXTURE,
  build,
  builtPages,
  read,
  scriptTagsOf,
} from "./helpers";

const REPO_ROOT = join(APP_ROOT, "..", "..");

/** A generated block, as text. */
function siteBlock(target: (typeof TARGETS)[keyof typeof TARGETS]): string {
  return readFileSync(join(CADDY_DIR, target.file), "utf-8");
}

/** A repo-root file, as text. */
function repoFile(name: string): string {
  return readFileSync(join(REPO_ROOT, name), "utf-8");
}

/** The value of one header the block sets, wherever in it that happens. */
function headerValue(block: string, name: string): string {
  const match = block.match(new RegExp(`^\\s*(?:header @\\w+ )?${name} "([^"]*)"`, "m"));
  return match?.[1] ?? "";
}

/** One CSP directive's sources, from the policy the block sets. */
function cspDirective(block: string, directive: string): string[] {
  const policy = headerValue(block, "Content-Security-Policy");
  const found = policy
    .split(";")
    .map((part) => part.trim())
    .find((part) => part === directive || part.startsWith(`${directive} `));
  return found === undefined ? [] : found.split(/\s+/).slice(1);
}

describe("the generated Caddy site block", () => {
  it("is written by a build, for both deployments", () => {
    for (const target of Object.values(TARGETS)) {
      expect(existsSync(join(CADDY_DIR, target.file))).toBe(true);
    }
  });

  it("serves the directory docker-compose mounts, on the host the app does not use", () => {
    expect(siteBlock(TARGETS.dev)).toContain(`root * ${SITE_ROOT}`);
    expect(siteBlock(TARGETS.dev)).toMatch(/^marketing\.localhost \{$/m);

    // The whole point of the host split: `localhost` is still, only, the app.
    // A Playwright spec, a ZAP target and a k6 script all name it.
    expect(siteBlock(TARGETS.dev)).not.toMatch(/^localhost \{$/m);
  });

  it("gets its own certificate locally and Caddy's automatic one in production", () => {
    expect(siteBlock(TARGETS.dev)).toContain("tls internal");
    expect(siteBlock(TARGETS.prod)).not.toContain("tls internal");
    expect(siteBlock(TARGETS.prod)).toContain("{$MARKETING_DOMAIN:marketing.localhost}");
  });

  it("carries its own security headers rather than inheriting the app's", () => {
    const block = siteBlock(TARGETS.dev);

    expect(headerValue(block, "Strict-Transport-Security")).toContain("max-age=31536000");
    expect(headerValue(block, "X-Content-Type-Options")).toBe("nosniff");
    expect(headerValue(block, "X-Frame-Options")).toBe("DENY");
    expect(headerValue(block, "Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(headerValue(block, "Permissions-Policy")).toContain("camera=()");
    expect(block).toContain("-Server");

    // Production asks for `preload`; a `.localhost` name cannot be preloaded
    // and there would be no point.
    expect(headerValue(siteBlock(TARGETS.prod), "Strict-Transport-Security")).toContain("preload");
  });
});

describe("the Content-Security-Policy", () => {
  /**
   * The hashes, recomputed here from the built pages rather than read back
   * out of the generator.
   *
   * `scriptTagsOf` is T-300's helper and already drops `application/ld+json`,
   * so this is also the assertion that a *data block* is not being hashed:
   * the site has 51 pages with a JSON-LD payload each, and a policy that
   * hashed those would have 50-odd entries and change on every copy edit.
   */
  function hashesInDist(): Set<string> {
    const hashes = new Set<string>();
    for (const page of builtPages()) {
      for (const tag of scriptTagsOf(read(page))) {
        if (/<script\b[^>]*\ssrc=/.test(tag)) {
          continue;
        }
        const content = tag.replace(/^<script\b[^>]*>/, "").replace(/<\/script>$/, "");
        hashes.add(`'sha256-${createHash("sha256").update(content, "utf-8").digest("base64")}'`);
      }
    }
    return hashes;
  }

  it("names every inline script the build actually produced", async () => {
    const expected = hashesInDist();
    const generated = await inlineScriptHashes(DIST);

    expect(new Set(generated)).toEqual(expected);
    // A regression guard on the *shape* of the answer: today the site ships
    // Astro's island runtime, its hydration loader and T-305's locale
    // redirect. A number in the dozens would mean the JSON-LD exclusion broke.
    expect(generated.length).toBeGreaterThan(0);
    expect(generated.length).toBeLessThan(10);
  });

  it("puts exactly those hashes in the header both deployments send", async () => {
    const generated = await inlineScriptHashes(DIST);

    for (const target of Object.values(TARGETS)) {
      expect(cspDirective(siteBlock(target), "script-src")).toEqual(["'self'", ...generated]);
    }
  });

  it("never buys those scripts with 'unsafe-inline'", () => {
    expect(cspDirective(siteBlock(TARGETS.dev), "script-src")).not.toContain("'unsafe-inline'");
  });

  it("allows inline style, because shiki's per-token style attributes need it", () => {
    // Not a lapse -- see contentSecurityPolicy() in the generator. The check
    // that keeps it honest is that the markup really does carry style
    // attributes a hash could not cover; the day it stops, this fails and the
    // exemption can go.
    expect(cspDirective(siteBlock(TARGETS.dev), "style-src")).toContain("'unsafe-inline'");

    const guide = read("en/guides/getting-started/installation/index.html");
    expect(guide).toMatch(/<span style="color:#[0-9A-Fa-f]{6}"/);
  });

  it("closes the four holes default-src does not cover", () => {
    const block = siteBlock(TARGETS.dev);

    for (const directive of ["base-uri", "form-action", "frame-ancestors", "object-src"]) {
      expect(cspDirective(block, directive)).toEqual(["'none'"]);
    }
    expect(cspDirective(block, "default-src")).toEqual(["'self'"]);
  });
});

describe("caching and indexing", () => {
  it("marks content-addressed assets immutable and nothing else", () => {
    const block = siteBlock(TARGETS.dev);

    expect(block).toContain("@immutable path /_astro/*");
    expect(block).toContain(
      'header @immutable Cache-Control "public, max-age=31536000, immutable"',
    );
    expect(block).toContain("@revalidated not path /_astro/*");
    expect(headerValue(block, "Cache-Control")).toBe("public, max-age=31536000, immutable");

    // The one that would actually hurt: an immutable HTML page cannot be
    // fixed for a year.
    const shortLived = block.match(/header @revalidated Cache-Control "([^"]*)"/)?.[1] ?? "";
    expect(shortLived).not.toContain("immutable");
    expect(shortLived).toMatch(/max-age=\d{1,3}\b/);
  });

  it("tells crawlers to stay away everywhere but the production host", () => {
    // T-304 left this half open: robots.txt says `Disallow: /` for a
    // non-production build, and the header is the part only a served host can
    // send. Locally that is unconditional; in production it is a rule about
    // the host in the request, so a production build served from staging is
    // still covered.
    expect(siteBlock(TARGETS.dev)).toContain('header X-Robots-Tag "noindex, nofollow"');

    const prod = siteBlock(TARGETS.prod);
    expect(prod).toContain(`@not_production not host ${new URL(PRODUCTION_ORIGIN).host}`);
    expect(prod).toContain('header @not_production X-Robots-Tag "noindex, nofollow"');
  });
});

describe("the locale-less URL redirect", () => {
  it("knows every enabled locale and no others", () => {
    const block = siteBlock(TARGETS.dev);
    const announced = [...block.matchAll(/@(\w+)_first header_regexp/g)].map((match) => match[1]);

    const sitemapLocales = [
      ...new Set(builtPages().map((page) => page.split("/")[0] ?? "")),
    ].filter((segment) => segment.length === 2);

    expect(new Set(announced)).toEqual(new Set(sitemapLocales));
  });

  it("follows the product's locale list rather than a second copy of it", () => {
    // Rendered directly with a different list: a product that turns a locale
    // off must not be left with an edge redirecting to pages it did not build.
    const rendered = renderSite(TARGETS.dev, {
      hashes: ["'sha256-x'"],
      locales: ["en", "fr"],
      defaultLocale: "fr",
      productionHost: "example.com",
    });

    expect(rendered).toContain("@en_first header_regexp");
    expect(rendered).toContain("@fr_first header_regexp");
    expect(rendered).not.toContain("@de_first header_regexp");
    expect(rendered).toContain("@built path /_astro/* /en /en/* /fr /fr/*");
    expect(rendered).toContain("redir * /fr{uri} 302");
  });

  it("is a 302 that varies, never a cached 301", () => {
    const block = siteBlock(TARGETS.dev);

    expect(block).toContain("header Vary Accept-Language");
    // A 301 here is cached per-URL by the browser, so the first visitor's
    // language would be pinned onto everyone who used that machine after them.
    expect(block).not.toMatch(/redir @\w+ \S+ 301/);
    for (const [, code] of block.matchAll(/redir (?:@\w+|\*) \S+ (\d{3})/g)) {
      expect(code).toBe("302");
    }
  });

  it("leaves a bare locale to file_server, which knows to add the slash", () => {
    // `path /en/*` does not match `/en`, so without the bare spelling beside
    // it the language switcher's own URL falls into the redirect below and is
    // answered with `/en/en/`. Found by curling the running stack, not by
    // reading the config.
    for (const locale of ["en", "fr", "ar"]) {
      expect(siteBlock(TARGETS.dev)).toContain(`/${locale} /${locale}/*`);
    }
  });

  it("leaves /.well-known/ out of it entirely", () => {
    // RFC 8615 URIs carry no locale, and one of them is the ACME HTTP-01
    // challenge. Caddy answers that itself ahead of these routes, so this is
    // about the rest: a security.txt in public/.well-known/ should be served,
    // not redirected into a language.
    expect(siteBlock(TARGETS.dev)).toContain("handle /.well-known/* {");
  });

  it("leaves a real file alone", () => {
    // robots.txt, the sitemap chunks and the OG card all live at the root
    // with no locale segment, and redirecting them into one would take
    // T-304's whole indexing story with it.
    expect(siteBlock(TARGETS.dev)).toContain("@file path_regexp \\.[A-Za-z0-9]+$");
  });

  it("answers an unmatched path with the built 404 page, at 404", () => {
    const block = siteBlock(TARGETS.dev);

    expect(block).toContain("handle_errors 404 {");
    expect(block).toContain("rewrite * /404.html");
    expect(block).toContain("status 404");
    expect(existsSync(join(DIST, "404.html"))).toBe(true);
  });
});

describe("the module toggle", () => {
  afterAll(() => {
    build();
  });

  it("takes the site block away with the site", () => {
    build();
    for (const target of Object.values(TARGETS)) {
      expect(existsSync(join(CADDY_DIR, target.file))).toBe(true);
    }

    const output = build({ productConfig: MINIMAL_FIXTURE });

    // The requirement is absence, not a 404: with no file, the Caddyfile's
    // glob import matches nothing and the edge comes up serving the app
    // exactly as it did before.
    expect(output).toContain("Caddy site block");
    for (const target of Object.values(TARGETS)) {
      expect(existsSync(join(CADDY_DIR, target.file))).toBe(false);
    }
  });
});

describe("the wiring around the block", () => {
  it("is imported by both Caddyfiles, as a glob", () => {
    // A glob that matches nothing is a warning in Caddy's log; a plain
    // `import` of a missing file is a fatal config error. That difference is
    // what makes "marketing is off" and "the frontend was never built" both
    // boot.
    for (const file of ["Caddyfile", "Caddyfile.prod"]) {
      expect(repoFile(file)).toContain("import /etc/caddy/conf.d/*.caddy");
    }
  });

  it("reaches dev's Caddy as a bind mount", () => {
    expect(repoFile("docker-compose.yml")).toContain(
      `- ./apps/marketing/dist:${SITE_ROOT}:ro`,
    );
    expect(repoFile("docker-compose.yml")).toContain(
      "- ./apps/marketing/caddy/dev:/etc/caddy/conf.d:ro",
    );
  });

  it("reaches production's Caddy by whichever of the two routes is in use", () => {
    // Since 2026-09-14 there are two, and the mounts had to *move* out of
    // docker-compose.prod.yml rather than be overridden: compose merges
    // `volumes` by appending, so an overlay cannot remove a base mount and a
    // leftover bind of ./apps/marketing/dist would shadow the copy baked
    // into the edge image. Both routes are asserted, not just the one this
    // deployment happens to use -- the other is exactly what rots unnoticed.
    expect(repoFile("docker-compose.host-build.yml")).toContain(
      `- ./apps/marketing/dist:${SITE_ROOT}:ro`,
    );
    expect(repoFile("docker-compose.host-build.yml")).toContain(
      "- ./apps/marketing/caddy/prod:/etc/caddy/conf.d:ro",
    );

    const dockerfile = repoFile("Dockerfile.caddy");
    expect(dockerfile).toContain(`/repo/apps/marketing/dist ${SITE_ROOT}`);
    expect(dockerfile).toContain("/repo/apps/marketing/caddy/prod /etc/caddy/conf.d");

    // And the base file must carry neither, or it shadows both.
    expect(repoFile("docker-compose.prod.yml")).not.toContain(SITE_ROOT);
    expect(repoFile("docker-compose.prod.yml")).not.toContain("/etc/caddy/conf.d");
  });

  it("is declared as a build output, so a cache hit cannot leave a stale one", () => {
    // Without this, `turbo build` restores dist/ from cache and leaves the
    // site blocks describing the build before it -- a CSP whose hashes match
    // nothing being served, silently. Same shape as the api-client bug next
    // door.
    const config = readFileSync(join(APP_ROOT, "turbo.json"), "utf-8");
    expect(config).toContain('"caddy/*/*.caddy"');
  });
});

/**
 * Is there a Docker daemon to run the real thing against?
 *
 * Probed once, at module load. CI has one; a developer machine may not, and a
 * skipped case that says so is better than a red suite that means "Docker is
 * not running".
 */
function dockerAvailable(): boolean {
  try {
    execFileSync("docker", ["version", "--format", "{{.Server.Version}}"], {
      stdio: "pipe",
      timeout: 30_000,
    });
    return true;
  } catch {
    return false;
  }
}

const HAS_DOCKER = dockerAvailable();

describe.skipIf(!HAS_DOCKER)("Caddy itself accepts the configuration", () => {
  /** `caddy validate` over a repo-root Caddyfile and the block it imports. */
  function validate(caddyfile: string, conf: string, env: Record<string, string>): string {
    return execFileSync(
      "docker",
      [
        "run",
        "--rm",
        ...Object.entries(env).flatMap(([key, value]) => ["-e", `${key}=${value}`]),
        "-v",
        `${join(REPO_ROOT, caddyfile)}:/etc/caddy/Caddyfile:ro`,
        "-v",
        `${join(CADDY_DIR, conf)}:/etc/caddy/conf.d:ro`,
        "caddy:2-alpine",
        "caddy",
        "validate",
        "--config",
        "/etc/caddy/Caddyfile",
        "--adapter",
        "caddyfile",
      ],
      { encoding: "utf-8", stdio: "pipe", timeout: 180_000 },
    );
  }

  it("loads the dev Caddyfile with the marketing block imported", () => {
    expect(validate("Caddyfile", "dev", {})).toContain("Valid configuration");
  });

  it("loads the prod Caddyfile with both hosts configured", () => {
    expect(
      validate("Caddyfile.prod", "prod", {
        DOMAIN: "app.example.com",
        MARKETING_DOMAIN: "example.com",
      }),
    ).toContain("Valid configuration");
  });
}, 300_000);
