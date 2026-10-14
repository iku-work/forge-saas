import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  adoptServerPreference,
  applyTheme,
  getPreference,
  getResolvedTheme,
  readStoredPreference,
  reconcileServerPreference,
  resolveTheme,
  setPreference,
  startThemeSync,
  subscribeTheme,
  systemTheme,
  THEME_BOOT_SNIPPET,
  THEME_STORAGE_KEY,
} from "./theme";

/** jsdom has no matchMedia. Installs one whose `matches` this test controls,
 *  and hands back the listener the store registered so a system change can be
 *  simulated. */
function stubMatchMedia(matches: boolean): { change: (next: boolean) => void } {
  const listeners = new Set<() => void>();
  let current = matches;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: current,
      media: query,
      onchange: null,
      addEventListener: (_: string, listener: () => void) => {
        listeners.add(listener);
      },
      removeEventListener: (_: string, listener: () => void) => {
        listeners.delete(listener);
      },
      dispatchEvent: () => false,
    }),
  });
  return {
    change: (next: boolean) => {
      current = next;
      for (const listener of listeners) {
        listener();
      }
    },
  };
}

function removeMatchMedia(): void {
  Reflect.deleteProperty(window, "matchMedia");
}

let teardown: (() => void) | null = null;

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
});

afterEach(() => {
  teardown?.();
  teardown = null;
  removeMatchMedia();
  vi.restoreAllMocks();
});

describe("systemTheme", () => {
  it("reads prefers-color-scheme", () => {
    stubMatchMedia(true);
    expect(systemTheme()).toBe("dark");

    stubMatchMedia(false);
    expect(systemTheme()).toBe("light");
  });

  it("falls back to light where matchMedia does not exist", () => {
    // jsdom's own state, and the reason darkMediaQuery() is a try/catch: the
    // call throws a TypeError rather than returning undefined. A store that
    // let that escape would take down every test file importing the shell.
    removeMatchMedia();
    expect(systemTheme()).toBe("light");
  });
});

describe("resolveTheme", () => {
  it("passes an explicit preference through without consulting the OS", () => {
    stubMatchMedia(true);
    expect(resolveTheme("light")).toBe("light");
    expect(resolveTheme("dark")).toBe("dark");
  });

  it("resolves system against the OS", () => {
    stubMatchMedia(true);
    expect(resolveTheme("system")).toBe("dark");
  });
});

describe("applyTheme", () => {
  it("sets both the class components select on and the UA colour-scheme", () => {
    applyTheme("dark");
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");

    applyTheme("light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });
});

describe("readStoredPreference", () => {
  it("defaults to system when nothing is stored", () => {
    expect(readStoredPreference()).toBe("system");
  });

  it("returns a stored preference", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    expect(readStoredPreference()).toBe("dark");
  });

  it("treats an unrecognised stored value as system", () => {
    // The value can be anything: it survives a downgrade, and it is one
    // devtools edit away from arbitrary. An unvalidated read would put
    // "solarized" on documentElement.
    window.localStorage.setItem(THEME_STORAGE_KEY, "solarized");
    expect(readStoredPreference()).toBe("system");
  });

  it("survives localStorage throwing", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("site data blocked");
    });
    expect(readStoredPreference()).toBe("system");
  });
});

describe("startThemeSync", () => {
  it("applies the stored preference on start", () => {
    stubMatchMedia(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");

    teardown = startThemeSync();

    expect(getPreference()).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("follows the OS while the preference is system", () => {
    const media = stubMatchMedia(false);
    teardown = startThemeSync();
    expect(document.documentElement).not.toHaveClass("dark");

    media.change(true);

    expect(getResolvedTheme()).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("ignores the OS once a preference is pinned", () => {
    // The point of pinning: a user who chose Light does not get flipped when
    // their machine switches to dark at sunset.
    const media = stubMatchMedia(false);
    teardown = startThemeSync();
    setPreference("light");

    media.change(true);

    expect(getResolvedTheme()).toBe("light");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("notifies subscribers when the OS changes under a system preference", () => {
    const media = stubMatchMedia(false);
    teardown = startThemeSync();
    const listener = vi.fn();
    const unsubscribe = subscribeTheme(listener);

    media.change(true);

    expect(listener).toHaveBeenCalled();
    unsubscribe();
  });
});

describe("setPreference", () => {
  it("stores, applies and reports the new preference", () => {
    stubMatchMedia(false);
    teardown = startThemeSync();

    setPreference("dark");

    expect(getPreference()).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("notifies subscribers", () => {
    stubMatchMedia(false);
    teardown = startThemeSync();
    const listener = vi.fn();
    const unsubscribe = subscribeTheme(listener);

    setPreference("dark");

    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});

describe("adoptServerPreference", () => {
  it("applies the account's saved choice", () => {
    stubMatchMedia(false);
    teardown = startThemeSync();

    adoptServerPreference("dark");

    expect(getPreference()).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("is a no-op when it already agrees with the local preference", () => {
    // The ordinary case -- same device, unchanged choice -- must not emit,
    // or every /auth/me resolution would re-render every subscriber.
    stubMatchMedia(false);
    teardown = startThemeSync();
    setPreference("dark");
    const listener = vi.fn();
    const unsubscribe = subscribeTheme(listener);

    adoptServerPreference("dark");

    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });
});

describe("reconcileServerPreference", () => {
  it("keeps this device's choice over an account still on the default", () => {
    // Picked dark, then signed up: a new account says "system", and that is
    // not a choice the user made.
    stubMatchMedia(false);
    teardown = startThemeSync();
    setPreference("dark");

    expect(reconcileServerPreference("system")).toBe("dark");
    expect(getPreference()).toBe("dark");
  });

  it("adopts an explicit account choice", () => {
    stubMatchMedia(false);
    teardown = startThemeSync();
    setPreference("dark");

    expect(reconcileServerPreference("light")).toBeNull();
    expect(getPreference()).toBe("light");
  });

  it("leaves a device that never chose on the account's default", () => {
    stubMatchMedia(false);
    teardown = startThemeSync();

    expect(reconcileServerPreference("system")).toBeNull();
    expect(getPreference()).toBe("system");
  });
});

describe("THEME_BOOT_SNIPPET", () => {
  it("matches the script inlined in index.html", async () => {
    // The snippet cannot be imported by the page -- it has to run
    // synchronously, before the deferred module bundle -- so the only thing
    // stopping the two copies drifting is this assertion.
    const html = await import("../../index.html?raw").then((m) => m.default);
    // Every run of whitespace collapses to one space, newlines included:
    // prettier owns the line breaks inside index.html (it re-wraps the `var
    // d = ...` expression across four lines) and this string owns its own, so
    // byte equality would fail on a reformat that changed nothing. What must
    // not drift is the source itself.
    const normalise = (source: string) => source.replace(/\s+/g, " ").trim();

    expect(normalise(html)).toContain(normalise(THEME_BOOT_SNIPPET));
  });

  it("resolves the same theme the store would, for each stored value", () => {
    // Runs the snippet itself rather than re-describing it: a boot script
    // that disagrees with the store is a visible flash-then-correct on every
    // load, which is exactly what it exists to prevent.
    for (const [stored, matchesDark, expected] of [
      ["dark", false, true],
      ["light", true, false],
      ["system", true, true],
      ["system", false, false],
      [null, true, true],
      ["nonsense", true, true],
    ] as const) {
      window.localStorage.clear();
      if (stored !== null) {
        window.localStorage.setItem(THEME_STORAGE_KEY, stored);
      }
      stubMatchMedia(matchesDark);
      document.documentElement.classList.remove("dark");

      // eslint-disable-next-line @typescript-eslint/no-implied-eval -- running the real snippet *is* the assertion
      const runBootSnippet = new Function(THEME_BOOT_SNIPPET) as () => void;
      runBootSnippet();

      expect(document.documentElement.classList.contains("dark")).toBe(
        expected,
      );
      expect(document.documentElement.style.colorScheme).toBe(
        expected ? "dark" : "light",
      );
    }
  });
});
