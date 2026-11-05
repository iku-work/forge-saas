import { describe, expect, it } from "vitest";

import { budgetsFor, findProblems, measure } from "./check-bundle-budget.mjs";

const FILE_SIZES = {
  entry: 100,
  shared: 10,
  login: 5,
  heavy: 40,
  en: 7,
  de: 9,
  css: 3,
};
const sizeOf = (file) => FILE_SIZES[file];

const MANIFEST = {
  "index.html": {
    file: "entry",
    isEntry: true,
    imports: ["_shared.js"],
    css: ["css"],
  },
  "_shared.js": { file: "shared" },
  "_heavy.js": { file: "heavy" },
  "src/routes/$locale.login.tsx?tsr-split=component": {
    file: "login",
    isDynamicEntry: true,
    imports: ["_shared.js"],
  },
  "src/routes/$locale.files.tsx?tsr-split=component": {
    file: "login",
    isDynamicEntry: true,
    imports: ["_heavy.js"],
  },
  "../../packages/i18n/locales/en/common.json": {
    file: "en",
    isDynamicEntry: true,
  },
  "../../packages/i18n/locales/de/common.json": {
    file: "de",
    isDynamicEntry: true,
  },
};

describe("measure", () => {
  const sizes = measure(MANIFEST, sizeOf);

  it("counts what the entry loads as the initial JS", () => {
    expect(sizes["initial JS"]).toBe(110);
  });

  it("charges a route only for what it adds to the initial JS", () => {
    expect(sizes["$locale.login.tsx"]).toBe(5);
  });

  it("charges a route for the shared chunks only it pulls in", () => {
    expect(sizes["$locale.files.tsx"]).toBe(45);
  });

  it("leaves out a lazy import the initial JS already contains", () => {
    const lazyShared = {
      ...MANIFEST,
      "_shared.js": { file: "shared", isDynamicEntry: true },
    };
    expect(Object.keys(measure(lazyShared, sizeOf))).not.toContain(
      "_shared.js",
    );
  });

  it("gives the locale catalogs one line, the largest", () => {
    expect(sizes["largest locale catalog"]).toBe(9);
    expect(
      Object.keys(sizes).filter((name) => name.includes("common")),
    ).toEqual([]);
  });

  it("adds up the stylesheets", () => {
    expect(sizes["CSS"]).toBe(3);
  });
});

describe("findProblems", () => {
  it("passes at exactly the budget", () => {
    expect(findProblems({ a: 10 }, { a: 10 })).toEqual([]);
  });

  it("fails one byte over", () => {
    expect(findProblems({ a: 11 }, { a: 10 })).toEqual(["a: 11 is over 10"]);
  });

  it("fails a new route with no budget, so nothing grows unmeasured", () => {
    expect(findProblems({ a: 1 }, {})).toEqual(["a: no budget"]);
  });

  it("fails a budget for something removed, so the file can't go stale", () => {
    expect(findProblems({}, { a: 1 })).toEqual([
      "a: budget for something no longer built",
    ]);
  });
});

describe("budgetsFor", () => {
  it("leaves 10% headroom, sorted so an update gives a small diff", () => {
    expect(JSON.stringify(budgetsFor({ b: 100, a: 11 }))).toBe(
      '{"a":13,"b":111}',
    );
  });
});
