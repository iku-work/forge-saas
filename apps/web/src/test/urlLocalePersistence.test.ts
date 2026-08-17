import { afterEach, beforeEach, describe, expect, it } from "vitest";

import i18next, {
  initI18n,
  LOCALE_STORAGE_KEY,
  markExplicitLocaleChoice,
  setUrlLocale,
} from "../i18n";

/**
 * T-233's rule: the URL locale wins for *rendering* a shared /de/... link, but
 * never overwrites the visitor's own stored preference. Only an explicit
 * switch does that.
 *
 * The rule is enforced in i18n.ts's `languageChanged` handler, which has to
 * decide whether the change it is being told about came from the URL or from
 * the user. That used to be inferred from a flag held across `changeLanguage`
 * -- a window -- and the event does not reliably arrive inside the window it
 * belongs to. Every case below is one the window got wrong; none of them
 * depends on timing any more.
 *
 * Both directions are covered because the flag broke both, and only one was
 * visible: -locale-routing.test.tsx caught the first intermittently, while the
 * second silently discarded a preference the user had explicitly chosen and
 * had no test at all.
 */
describe("URL-driven locale changes vs. the stored preference", () => {
  beforeEach(async () => {
    await initI18n();
    markExplicitLocaleChoice(); // clear whatever a previous test marked
    if (i18next.language !== "en") {
      await i18next.changeLanguage("en");
    }
    localStorage.setItem(LOCALE_STORAGE_KEY, "en");
  });

  afterEach(async () => {
    markExplicitLocaleChoice();
    if (i18next.language !== "en") {
      await i18next.changeLanguage("en");
    }
    localStorage.removeItem(LOCALE_STORAGE_KEY);
  });

  it("leaves the stored preference alone when two URL-driven changes overlap", async () => {
    // Two concurrent applications of the same URL locale, exactly as a
    // pending navigation plus a revalidation produce.
    await Promise.all([setUrlLocale("de"), setUrlLocale("de")]);

    expect(i18next.language).toBe("de");
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
  });

  it("ignores a languageChanged event that arrives after the change settled", async () => {
    // i18next can emit for a change that is no longer the current one -- an
    // abandoned navigation's bundle finishing late. Emitted directly here
    // because the timing that produces it naturally is exactly the timing
    // that made -locale-routing.test.tsx flaky rather than failing.
    await setUrlLocale("de");
    i18next.emit("languageChanged", "de");

    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
  });

  it("persists an explicit switch to a locale the URL drove earlier", async () => {
    // The visitor follows a shared /de/ link and then decides they do want
    // German. RoutedLocaleSwitcher calls markExplicitLocaleChoice() first,
    // which is the only thing separating this from the case above.
    await setUrlLocale("de");

    markExplicitLocaleChoice();
    await i18next.changeLanguage("de");

    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("de");
  });

  it("persists an explicit switch that overlaps a URL-driven change", async () => {
    // The mirror image of the flake: the user picks a language while a
    // URL-driven change is mid-flight. `useLocale().setLocale` is a bare
    // changeLanguage() call (it is platform-agnostic and knows nothing about
    // localStorage), so this is what that hook does.
    const urlDriven = setUrlLocale("de");
    markExplicitLocaleChoice();
    await i18next.changeLanguage("fi");
    await urlDriven;

    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("fi");
  });
});
