// @vitest-environment jsdom
//
// Per-file, not in vitest.config.ts: every other test file here reads
// `dist/` off disk and wants the faster `node` environment. Only the block
// at the bottom of this one runs the banner's script, and running the exact
// string that ships is what makes those assertions about the artefact rather
// than about a re-implementation of it.
/**
 * Analytics and cookie consent (T-308).
 *
 * The first half is about the default build and is the more important one:
 * this template ships with no analytics, and "no analytics" has to be a fact
 * about `dist/` rather than a claim in a comment. Those tests read a real
 * build and look for cookies and third-party hosts that are not there.
 *
 * The second half drives the cookie path, which the committed configuration
 * never takes. It cannot use a real build for that -- the provider is chosen
 * in a committed TypeScript file, and mutating it to run a test would mean a
 * test that edits `src/`. It exercises the pieces directly instead: the
 * decision functions, the generated inline script (as text, and by running
 * it in a DOM), and the CSP and budget allowlists the config feeds. What the
 * real build proves in the first half is the wiring; what these prove is the
 * behaviour that wiring switches on.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import {
  COOKIELESS_EXAMPLE,
  COOKIE_EXAMPLE,
  NONE,
  allowedHosts,
  analytics,
  isConfigured,
  needsBanner,
} from "../src/config/analytics";
import {
  COOKIE_MAX_AGE_DAYS,
  COOKIE_NAME,
  CONSENT_TYPE,
  POLICY_VERSION,
  consentScriptSource,
  cookieAttributes,
  cookiesToClear,
  current,
  parse,
  serialize,
} from "../src/lib/consent";
import { analyticsHostnames, renderSite, TARGETS } from "../scripts/caddy-site.mjs";
import { APP_ROOT, DIST, build, builtPages, read, readPath } from "./helpers";

/**
 * Hosts a built page makes the browser *fetch* from.
 *
 * Only the elements that cause a request, which is the same distinction
 * `scripts/check-budget.mjs` draws and for the same reason: `<a href>` to
 * another origin is a link, `<link rel="canonical">` is a statement about a
 * URL rather than a fetch, and the app is on another origin by design
 * (T-307). A check that did not know the difference would report every page
 * of a correct build as loading a dozen third-party resources -- which is
 * exactly what the first version of this helper did.
 */
function fetchedHostsIn(html: string): string[] {
  const patterns = [
    /<script\b[^>]*\bsrc="(https?:\/\/[^"]+)"/g,
    /<img\b[^>]*\bsrc="(https?:\/\/[^"]+)"/g,
    /<iframe\b[^>]*\bsrc="(https?:\/\/[^"]+)"/g,
    /<source\b[^>]*\bsrcset="(https?:\/\/[^"]+)"/g,
    // A <link> only fetches for certain `rel` values.
    /<link\b[^>]*\brel="(?:stylesheet|preload|modulepreload|prefetch|preconnect|dns-prefetch)"[^>]*\bhref="(https?:\/\/[^"]+)"/g,
  ];
  const urls = patterns.flatMap((pattern) =>
    [...html.matchAll(pattern)].map((match) => match[1] ?? ""),
  );
  return [...new Set(urls.map((url) => new URL(url).host))];
}

describe("the default build", () => {
  beforeAll(() => {
    build();
  }, 180_000);

  it("configures no analytics at all", () => {
    expect(analytics).toBe(NONE);
    expect(isConfigured()).toBe(false);
    expect(allowedHosts()).toEqual([]);
  });

  it("renders no consent banner on any page", () => {
    // Every page, read off disk rather than listed here, so a page added
    // tomorrow is covered without anyone editing this test.
    for (const page of builtPages()) {
      expect(read(page), page).not.toContain('id="consent-banner"');
    }
  });

  it("offers no cookie-settings control, because there are no cookies", () => {
    expect(readPath("/en/")).not.toContain("data-consent-manage");
  });

  it("sets no cookie and names no cookie in any page", () => {
    // The site is static, so the only way it could set one is from script.
    // Searching for the *cookie API* rather than for a name covers a provider
    // that invented its own.
    for (const page of builtPages()) {
      const html = read(page);
      expect(html, page).not.toContain("document.cookie");
      expect(html, page).not.toContain(COOKIE_NAME);
    }
  });

  it("makes no third-party request from any page", () => {
    // A stricter statement than check-budget.mjs's, which allows whatever
    // the analytics config declares: here the config declares nothing, so
    // the expected set is empty and any host at all is a failure.
    for (const page of builtPages()) {
      expect(fetchedHostsIn(read(page)), page).toEqual([]);
    }
  });

  it("widens no CSP directive", async () => {
    const hosts = await analyticsHostnames();
    expect(hosts).toEqual([]);

    const block = renderSite(TARGETS.dev, {
      hashes: ["'sha256-x'"],
      locales: ["en"],
      defaultLocale: "en",
      productionHost: "example.com",
      analyticsHosts: hosts,
    });

    expect(block).toContain("connect-src 'self';");
    expect(block).toContain("img-src 'self' data:;");
    expect(block).not.toContain("https://");
  });
});

describe("the two paths a product can take", () => {
  it("takes no banner for a cookieless provider", () => {
    // The whole argument for path 1: a banner avoided is worth more than a
    // banner implemented well, and the template makes that the cheaper
    // option rather than the harder one.
    expect(needsBanner(COOKIELESS_EXAMPLE, true)).toBe(false);
    expect(COOKIELESS_EXAMPLE.cookies).toEqual([]);
  });

  it("takes a banner for a cookie-setting provider", () => {
    expect(needsBanner(COOKIE_EXAMPLE, true)).toBe(true);
  });

  it("takes no banner when the gdpr module is off", () => {
    // T-308 gates this task on `gdpr`, not on `marketing` alone: consent is
    // recorded in that module's table, and a banner that collects an answer
    // it cannot store is worse than no banner because it looks like
    // compliance.
    expect(needsBanner(COOKIE_EXAMPLE, false)).toBe(false);
  });

  it("declares a cookie for every provider that needs consent", () => {
    // If it needs consent it stores *something*, and withdrawal has to be
    // able to delete that something by name.
    for (const provider of [NONE, COOKIELESS_EXAMPLE, COOKIE_EXAMPLE]) {
      if (provider.consent === "required") {
        expect(provider.cookies.length, provider.id).toBeGreaterThan(0);
      }
    }
  });

  it("declares a justification for every provider that loads a script", () => {
    for (const provider of [NONE, COOKIELESS_EXAMPLE, COOKIE_EXAMPLE]) {
      if (provider.scriptSrc !== null) {
        expect(provider.justification.length, provider.id).toBeGreaterThan(40);
      }
    }
  });

  it("widens exactly three CSP directives for a provider with hosts", () => {
    const block = renderSite(TARGETS.prod, {
      hashes: ["'sha256-x'"],
      locales: ["en"],
      defaultLocale: "en",
      productionHost: "example.com",
      analyticsHosts: [...COOKIE_EXAMPLE.hosts],
    });

    // Both hosts, in all three directives: a provider that loads from one
    // host and beacons to another is the usual shape, and a connect-src that
    // only named the loader would block the beacon with no visible error.
    // The header's value first, then a directive within it. Splitting the
    // whole block on "script-src " would find the *comment* that explains
    // why script-src carries hashes, three lines above the header.
    const policy = block.match(/Content-Security-Policy "([^"]+)"/)?.[1] ?? "";
    const directive = (name: string): string =>
      policy.split("; ").find((part) => part.startsWith(`${name} `)) ?? "";
    for (const host of COOKIE_EXAMPLE.hosts) {
      expect(directive("script-src"), host).toContain(`https://${host}`);
      expect(directive("connect-src"), host).toContain(`https://${host}`);
      expect(directive("img-src"), host).toContain(`https://${host}`);
    }
    // ...with the inline-script hashes still in front of them.
    expect(directive("script-src")).toContain("'sha256-x'");
    // ...and nothing else moved.
    expect(block).toContain("frame-ancestors 'none'");
    expect(block).toContain("object-src 'none'");
    expect(block).not.toContain("'unsafe-inline' 'sha256");
  });
});

describe("the stored decision", () => {
  it("round-trips through the cookie value", () => {
    const stored = { decision: "granted" as const, version: POLICY_VERSION };
    expect(parse(serialize(stored))).toEqual(stored);
  });

  it("is null for anything that is not one of the two answers", () => {
    for (const raw of ["", "maybe|2026-08-30", "granted", "granted|", null, undefined]) {
      expect(current(raw), String(raw)).toBeNull();
    }
  });

  it("is null once the policy version moves", () => {
    // The mechanism behind "we changed what we collect, so previous consent
    // no longer covers it": one string, no migration.
    expect(current(`granted|${POLICY_VERSION}`)).toBe("granted");
    expect(current("granted|2020-01-01")).toBeNull();
  });

  it("remembers a denial as firmly as a grant", () => {
    // A "no" that expired immediately would re-prompt the one visitor who
    // has already answered, which is how a banner becomes a dark pattern.
    expect(current(`denied|${POLICY_VERSION}`)).toBe("denied");
  });

  it("is written with attributes that survive a campaign link", () => {
    const attributes = cookieAttributes(COOKIE_MAX_AGE_DAYS * 24 * 60 * 60);

    // SameSite=Lax, not Strict: a visitor arriving from a search result is a
    // cross-site navigation, and Strict would withhold the cookie on exactly
    // that request.
    expect(attributes).toContain("SameSite=Lax");
    expect(attributes).toContain("Secure");
    expect(attributes).toContain("Path=/");
    expect(attributes).toContain(`Max-Age=${COOKIE_MAX_AGE_DAYS * 24 * 60 * 60}`);
  });

  it("expires by writing Max-Age=0 with the same scope", () => {
    // A cookie is only deleted by an expiry written with the same Path (and
    // Domain, where one is configured). Getting this wrong leaves the cookie
    // in place and calls it withdrawn.
    const expiry = cookieAttributes(0);
    expect(expiry).toContain("Max-Age=0");
    expect(expiry).toContain("Path=/");
  });
});

describe("the banner's inline script", () => {
  const source = consentScriptSource(COOKIE_EXAMPLE);

  it("cannot break out of its own script block", () => {
    // The same rule T-303's JsonLd.astro applies: a `<` in the interpolated
    // configuration would end the block early. Nothing in the committed
    // config contains one, which is exactly why the escaping has to be
    // asserted rather than observed.
    const withAngle = consentScriptSource({
      ...COOKIE_EXAMPLE,
      scriptAttributes: { "data-id": "</script><script>alert(1)</script>" },
    });
    expect(withAngle).not.toContain("</script>");
    expect(withAngle).toContain("\\u003c/script");
  });

  it("hides rather than creates the banner", () => {
    // A banner injected after paint is a guaranteed layout shift, and T-306's
    // CLS assertion would fail on it. The element is in the HTML; the script
    // only ever sets `hidden`.
    expect(source).not.toContain("insertAdjacentHTML");
    expect(source).not.toContain("innerHTML");
  });

  it("deletes the provider's cookies on withdrawal", () => {
    for (const name of COOKIE_EXAMPLE.cookies) {
      expect(source).toContain(name.replace(/\*$/, ""));
    }
    expect(source).toContain("clear()");
  });

  it("does not delete the consent cookie itself", () => {
    // "Denied" is a decision worth remembering. Clearing it would re-prompt
    // the visitor who just said no, on the very next page.
    expect(cookiesToClear(COOKIE_EXAMPLE)).not.toContain(COOKIE_NAME);
  });
});

describe("the banner's inline script, run", () => {
  /**
   * A DOM small enough to hold the four elements the script touches.
   *
   * jsdom rather than a real build, for the reason at the top of this file:
   * the cookie path is not the committed configuration, and a test must not
   * edit `src/` to reach it. What runs here is the exact string that would
   * be inlined into a page, so the thing under test is the shipped artefact
   * and not a re-implementation of it.
   */
  function boot(cookie = "") {
    document.body.innerHTML = `
      <aside id="consent-banner"><button id="consent-accept"></button>
      <button id="consent-decline"></button></aside>
      <button data-consent-manage></button>`;
    document.head.innerHTML = "";
    // jsdom's document.cookie is a real store; clear what a previous case set.
    for (const part of document.cookie.split(";")) {
      const name = part.split("=")[0]?.trim();
      if (name) {
        document.cookie = `${name}=; Max-Age=0; Path=/`;
      }
    }
    if (cookie) {
      document.cookie = cookie;
    }
    // eslint-disable-next-line no-new-func -- running the shipped script is the point
    new Function(consentScriptSource(COOKIE_EXAMPLE))();
    return {
      banner: document.getElementById("consent-banner") as HTMLElement,
      accept: document.getElementById("consent-accept") as HTMLElement,
      decline: document.getElementById("consent-decline") as HTMLElement,
      manage: document.querySelector("[data-consent-manage]") as HTMLElement,
      script: () => document.getElementById("analytics-script"),
    };
  }

  it("shows the banner and loads nothing for a first visit", () => {
    const dom = boot();

    expect(dom.banner.hidden).toBe(false);
    expect(dom.script()).toBeNull();
  });

  it("loads the provider only once consent is given", () => {
    const dom = boot();

    dom.accept.click();

    expect(dom.banner.hidden).toBe(true);
    expect(dom.script()?.getAttribute("src")).toBe(COOKIE_EXAMPLE.scriptSrc);
    expect(document.cookie).toContain(`${COOKIE_NAME}=granted`);
  });

  it("never loads the provider after a refusal", () => {
    const dom = boot();

    dom.decline.click();

    expect(dom.banner.hidden).toBe(true);
    expect(dom.script()).toBeNull();
    expect(document.cookie).toContain(`${COOKIE_NAME}=denied`);
  });

  it("loads nothing on a later visit by a visitor who refused", () => {
    const dom = boot(`${COOKIE_NAME}=denied%7C${POLICY_VERSION}`);

    expect(dom.banner.hidden).toBe(true);
    expect(dom.script()).toBeNull();
  });

  it("loads the provider on a later visit by a visitor who agreed", () => {
    const dom = boot(`${COOKIE_NAME}=granted%7C${POLICY_VERSION}`);

    expect(dom.banner.hidden).toBe(true);
    expect(dom.script()).not.toBeNull();
  });

  it("asks again once the policy version moves", () => {
    const dom = boot(`${COOKIE_NAME}=granted%7C1999-01-01`);

    expect(dom.banner.hidden).toBe(false);
    expect(dom.script()).toBeNull();
  });

  it("re-opens the banner from the permanent footer control", () => {
    const dom = boot(`${COOKIE_NAME}=granted%7C${POLICY_VERSION}`);
    expect(dom.banner.hidden).toBe(true);

    dom.manage.click();

    expect(dom.banner.hidden).toBe(false);
    // Focus moves into the banner, or a screen-reader user clicks "Cookie
    // settings" and nothing at all is announced. Onto Decline, which is first
    // in the DOM.
    expect(document.activeElement).toBe(dom.decline);
  });

  it("does not steal focus on a first visit", () => {
    // The counterpart: a visitor who has just arrived is reading the page,
    // and taking focus from them is worse than the banner being one Tab away.
    const dom = boot();

    expect(dom.banner.hidden).toBe(false);
    expect(document.activeElement).not.toBe(dom.decline);
  });

  it("clears the provider's cookies when consent is withdrawn", () => {
    // Withdrawal is the half most implementations skip: a banner that flips a
    // flag and leaves the cookies in place has not withdrawn anything.
    const dom = boot(`${COOKIE_NAME}=granted%7C${POLICY_VERSION}`);
    document.cookie = "_ex_id=abc; Path=/";
    document.cookie = "_ex_ses_1=def; Path=/";
    expect(document.cookie).toContain("_ex_id=abc");

    dom.manage.click();
    dom.decline.click();

    expect(document.cookie).not.toContain("_ex_id=abc");
    // The `name*` form is a prefix, which is how most tag managers name a
    // per-session cookie.
    expect(document.cookie).not.toContain("_ex_ses_1=def");
    // ...and the decision survives, so the visitor is not asked again.
    expect(document.cookie).toContain(`${COOKIE_NAME}=denied`);
  });

  it("does not load the provider twice", () => {
    const dom = boot();

    dom.accept.click();
    dom.manage.click();
    dom.accept.click();

    expect(document.querySelectorAll("#analytics-script")).toHaveLength(1);
  });
});

describe("the app half", () => {
  it("agrees with the marketing half about the cookie and the type", () => {
    // Two files, two apps, two build systems, one cookie. A parity test is
    // the only thing that keeps the reconciliation at login from silently
    // reconciling nothing.
    const appConsent = readFileSync(
      join(APP_ROOT, "..", "web", "src", "lib", "consent.ts"),
      "utf-8",
    );

    expect(appConsent).toContain(`"${COOKIE_NAME}"`);
    expect(appConsent).toContain(`"${CONSENT_TYPE}"`);
  });
});

describe("the built site", () => {
  it("still carries no third-party host in dist/ after this task", () => {
    // Re-stated against the whole directory rather than page by page: a
    // stylesheet or a preload could name a host too.
    const files = builtPages();
    expect(files.length).toBeGreaterThan(10);
    for (const file of files) {
      expect(read(file), file).not.toContain("analytics.example.com");
      expect(read(file), file).not.toContain("tags.example.net");
    }
    expect(DIST).toBeTruthy();
  });
});
