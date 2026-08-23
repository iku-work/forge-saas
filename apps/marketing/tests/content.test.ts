/**
 * T-301's content collections: the frontmatter contract, drafts, and MDX.
 *
 * The whole reason to use collections instead of a glob is that a bad post
 * fails the build with a message naming the file, so the first test here is
 * the one that justifies the design.
 */
import { spawn } from "node:child_process";
import { rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  APP_ROOT,
  build,
  buildExpectingFailure,
  decodeEntities,
  read,
  readPath,
} from "./helpers";

const BLOG_EN = join(APP_ROOT, "src", "content", "blog", "en");
const BROKEN_POST = join(BLOG_EN, "__broken-fixture.mdx");

describe("the frontmatter contract", () => {
  afterAll(() => {
    rmSync(BROKEN_POST, { force: true });
    build();
  });

  it("fails the build, naming the file, when a post has no publishedAt", () => {
    writeFileSync(
      BROKEN_POST,
      [
        "---",
        "title: A post with no date",
        "description: It should not be possible to publish this.",
        "author: The fixture",
        "---",
        "",
        "Body.",
        "",
      ].join("\n"),
      "utf-8",
    );

    const output = buildExpectingFailure();

    // Naming the file is the point. "data does not match collection schema"
    // on its own sends an author looking through every post they touched.
    expect(output).toContain("__broken-fixture");
    expect(output).toContain("publishedAt");
    expect(output).toContain("must be a date like 2026-08-22");
  });
});

describe("MDX", () => {
  beforeAll(() => {
    rmSync(BROKEN_POST, { force: true });
    build();
  });

  it("renders a guide including its fenced code block", () => {
    const html = readPath("/guides/getting-started/installation/");

    expect(html).toContain("Installation");
    // Highlighted, in a <pre> -- not an escaped blob of backticks, which is
    // what a Markdown pipeline that lost its syntax highlighter produces.
    expect(html).toMatch(/<pre[^>]*class="[^"]*astro-code/);
    expect(html).toContain("pnpm install");
  });

  it("routes a nested guide slug without a route per level", () => {
    // `[...slug]`, so `deploying/production` is one route file rather than a
    // directory of them -- documentation grows a hierarchy sooner than a blog.
    expect(readPath("/guides/deploying/production/")).toContain(
      "Going to production",
    );
  });

  it("groups the guides index by section, in `order`", () => {
    const html = readPath("/guides/");
    const installation = html.indexOf("Installation");
    const configuration = html.indexOf("Configuring your product");
    const production = html.indexOf("Going to production");

    expect(installation).toBeGreaterThan(-1);
    expect(installation).toBeLessThan(configuration);
    expect(configuration).toBeLessThan(production);
    expect(html).toContain("Getting started");
    expect(html).toContain("Deploying");
  });

  it("styles prose from the shared token layer rather than its own palette", () => {
    const html = readPath("/guides/getting-started/installation/");
    expect(html).toMatch(/class="prose[^"]*"/);
  });
});

describe("per-locale content", () => {
  beforeAll(() => {
    build();
  });

  it("publishes a post only in the locales it was translated into", () => {
    expect(readPath("/de/blog/module-toggles-in-practice/")).toContain(
      "Ein Modulschalter",
    );
    // Untranslated is *absent*, not English served under a French URL --
    // T-305's rule, and the reason src/content is keyed by locale.
    expect(() => readPath("/fr/blog/module-toggles-in-practice/")).toThrow();
  });

  it("says so, in the visitor's language, when a locale has no posts", () => {
    expect(decodeEntities(readPath("/fr/blog/"))).toContain(
      "Rien n'a encore été publié",
    );
    expect(readPath("/ar/guides/")).toContain("لم يُترجم أي دليل");
  });

  it("falls back to the original for a legal document, and says it did", () => {
    const html = decodeEntities(readPath("/fr/privacy/"));

    // The deliberate exception to the no-fallback rule: a policy is linked
    // from every footer, so a 404 there is worse than the authoritative
    // English text with a notice. See legalDocument() in src/lib/content.ts.
    expect(html).toContain("Ce document n'est pas encore traduit");
    expect(html).toContain('lang="en" dir="ltr"');
  });

  it("does not show that notice on the original", () => {
    expect(readPath("/privacy/")).not.toContain("has not been translated yet");
  });
});

describe("drafts", () => {
  const DRAFT_PATH = "/blog/the-next-release/";

  beforeAll(() => {
    build();
  });

  it("are absent from a production build, and from its blog index", () => {
    expect(() => readPath(DRAFT_PATH)).toThrow();
    expect(readPath("/blog/")).not.toContain(
      "What is coming in the next release",
    );
    expect(read("sitemap-0.xml")).not.toContain("the-next-release");
  });

  it("render in dev, with a badge", async () => {
    const server = await startDevServer();
    try {
      const page = await fetchText(`${server.origin}${DRAFT_PATH}`);

      expect(page).toContain("What is coming in the next release");
      expect(page).toContain("Draft");

      const index = await fetchText(`${server.origin}/blog/`);
      expect(index).toContain("What is coming in the next release");
    } finally {
      server.stop();
    }
  });
});

interface DevServer {
  origin: string;
  stop: () => void;
}

/**
 * Starts tests/dev-server.mjs and waits for it to report its port.
 *
 * The environment it gets is this one minus vitest's own markers, and that
 * subtraction is load-bearing rather than tidiness.
 *
 * vitest mirrors its `import.meta.env` into `process.env` -- `MODE=test`,
 * `DEV=1`, `PROD=`, alongside `NODE_ENV=test` and `VITEST=true`. Astro builds
 * the server-side `import.meta.env` from `process.env`, so a dev server
 * spawned with those inherited reports `MODE=test`, and `isPublished` in
 * src/lib/content.ts correctly concludes it is not in development and hides
 * the drafts. The test then fails for a reason that has nothing to do with
 * what it is checking. `VITEST` matters separately: with it set, Vite resolves
 * configuration differently and the server comes up serving nothing at all.
 *
 * Stripping them gives the child what a developer's terminal gives `astro
 * dev`, which is the situation under test. See the header of dev-server.mjs
 * for why this is a separate process in the first place.
 */
const VITEST_ENV_LEAKS = ["NODE_ENV", "MODE", "DEV", "PROD", "SSR"];

async function startDevServer(): Promise<DevServer> {
  const env = { ...process.env };
  for (const name of Object.keys(env)) {
    if (VITEST_ENV_LEAKS.includes(name) || name.startsWith("VITEST")) {
      delete env[name];
    }
  }

  const child = spawn(
    process.execPath,
    [join(APP_ROOT, "tests", "dev-server.mjs")],
    {
      cwd: APP_ROOT,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  const stop = () => {
    child.kill();
  };

  try {
    const port = await new Promise<number>((resolve, reject) => {
      let output = "";
      const timer = setTimeout(() => {
        reject(new Error(`dev server did not start in time:\n${output}`));
      }, 60_000);
      const finish = (error: Error) => {
        clearTimeout(timer);
        reject(error);
      };
      child.stdout.on("data", (chunk: Buffer) => {
        output += chunk.toString();
        const match = /\{"port":(\d+)\}/.exec(output);
        if (match?.[1]) {
          clearTimeout(timer);
          resolve(Number(match[1]));
        }
      });
      child.stderr.on("data", (chunk: Buffer) => {
        output += chunk.toString();
      });
      child.on("error", finish);
      child.on("exit", (code) => {
        finish(new Error(`dev server exited with ${String(code)}:\n${output}`));
      });
    });
    return { origin: `http://localhost:${String(port)}`, stop };
  } catch (error) {
    stop();
    throw error;
  }
}

async function fetchText(url: string): Promise<string> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${url} -> ${String(response.status)}`);
  }
  return response.text();
}
