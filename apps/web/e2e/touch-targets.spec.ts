// F-014: WCAG 2.2 2.5.8 Target Size (Minimum) -- every interactive element
// must present a target of at least 24x24 CSS pixels.
//
// Measured in one page.evaluate() rather than through per-element locators,
// for two reasons. Playwright's boundingBox() is a round trip each, and a
// page has upwards of fifty interactive elements; and this suite may only use
// data-testid selectors (CI greps for anything else), which is exactly the
// wrong tool for "find everything that can be clicked".
//
// What the measurement actually takes is the union of the element's own box
// and any ::before/::after pseudo-element it uses to enlarge its hit area --
// the technique the small icon buttons here rely on, where the glyph stays
// 16px and the target is 40px. getBoundingClientRect() alone does not see
// that, and would report a false failure on a correctly-built control.
import { expect, test } from "@playwright/test";

import { waitForLink } from "./helpers/mailhog";

/** WCAG 2.2 2.5.8 (AA). 44 is the Apple HIG figure and the level this
 *  template aims for on new controls, but 24 is the conformance line and what
 *  is enforced here -- a dense list legitimately sits between the two. */
const MIN_TARGET_PX = 24;

interface UndersizedTarget {
  tag: string;
  testId: string | null;
  label: string;
  width: number;
  height: number;
}

/**
 * The exceptions WCAG 2.5.8 itself grants, applied here:
 *
 *  - "Inline": a control inside a sentence, sized by the text flow. A link in
 *    a paragraph is exempt, and this app has several (the legal copy under
 *    the register form, the "resend" links in auth states).
 *  - "Equivalent": another control on the same page does the same thing at
 *    full size.
 *  - "User agent control": size comes from the browser, e.g. a bare
 *    checkbox's own box.
 *
 * Only the first is detectable from the DOM, so it is the only one automated:
 * an <a> whose parent is a text-bearing block is treated as inline. The rest
 * would be an allowlist, and an allowlist that nobody revisits is how this
 * kind of check quietly stops testing anything.
 */
async function undersizedTargets(page: import("@playwright/test").Page) {
  return page.evaluate((minPx: number) => {
    const SELECTOR = [
      "a[href]",
      "button",
      "input:not([type=hidden])",
      "select",
      "textarea",
      "[role=button]",
      "[role=link]",
      "[role=checkbox]",
      "[role=radio]",
      "[role=menuitem]",
      "[role=option]",
      "[role=tab]",
      "[role=switch]",
    ].join(",");

    const results: UndersizedTarget[] = [];

    for (const element of Array.from(document.querySelectorAll(SELECTOR))) {
      const htmlElement = element as HTMLElement;
      const style = window.getComputedStyle(htmlElement);
      // Not rendered at all: display:none, a collapsed dialog, a menu that
      // has not been opened. Nothing to measure and nothing to click.
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        style.pointerEvents === "none"
      ) {
        continue;
      }
      const box = htmlElement.getBoundingClientRect();
      // 1x1 is `sr-only`: the skip link until it is focused. Not a pointer
      // target; once focused it renders at full size.
      if (box.width <= 1 && box.height <= 1) {
        continue;
      }
      // Disabled controls are not targets. 2.5.8 exempts them by the same
      // reasoning it exempts anything the user cannot operate.
      if (htmlElement.hasAttribute("disabled")) {
        continue;
      }

      // Inline exception: a link sitting in running text.
      if (htmlElement.tagName === "A") {
        const parent = htmlElement.parentElement;
        const parentDisplay = parent
          ? window.getComputedStyle(parent).display
          : "";
        const isInTextFlow =
          parent !== null &&
          (parentDisplay === "block" || parentDisplay === "inline") &&
          (parent.textContent ?? "").trim() !==
            (htmlElement.textContent ?? "").trim();
        if (isInTextFlow) {
          continue;
        }
      }

      // Union with the pseudo-elements, which is where an expanded hit area
      // lives. `content` is "none" when the pseudo-element is not generated,
      // and an absolutely-positioned one with a negative inset reports its
      // own offsets as lengths we can add back on.
      let width = box.width;
      let height = box.height;
      for (const pseudo of ["::before", "::after"]) {
        const pseudoStyle = window.getComputedStyle(htmlElement, pseudo);
        if (
          pseudoStyle.content === "none" ||
          pseudoStyle.position !== "absolute"
        ) {
          continue;
        }
        const inset = (value: string): number => {
          const parsed = Number.parseFloat(value);
          return Number.isNaN(parsed) ? 0 : parsed;
        };
        width = Math.max(
          width,
          box.width - inset(pseudoStyle.left) - inset(pseudoStyle.right),
        );
        height = Math.max(
          height,
          box.height - inset(pseudoStyle.top) - inset(pseudoStyle.bottom),
        );
      }

      if (width + 0.5 < minPx || height + 0.5 < minPx) {
        results.push({
          tag: htmlElement.tagName.toLowerCase(),
          testId: htmlElement.getAttribute("data-testid"),
          label:
            htmlElement.getAttribute("aria-label") ??
            (htmlElement.textContent ?? "").trim().slice(0, 40),
          width: Math.round(width * 10) / 10,
          height: Math.round(height * 10) / 10,
        });
      }
    }
    return results;
  }, MIN_TARGET_PX);
}

function describe(targets: UndersizedTarget[]): string {
  return targets
    .map(
      (target) =>
        `  <${target.tag}> ${target.testId ? `[${target.testId}] ` : ""}"${target.label}" — ${target.width}x${target.height}`,
    )
    .join("\n");
}

const runId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
const email = `targets+${runId}@example.com`;
const password = `Targets-${runId}!Aa1`;

test("every interactive target is at least 24x24 (WCAG 2.2 2.5.8)", async ({
  page,
}) => {
  const check = async (name: string): Promise<void> => {
    const undersized = await undersizedTargets(page);
    expect(
      undersized,
      `${name} has targets under ${MIN_TARGET_PX}x${MIN_TARGET_PX}:\n${describe(undersized)}`,
    ).toEqual([]);
  };

  await page.goto("/login");
  await expect(page.getByTestId("login-email-input")).toBeVisible();
  await check("login");

  await page.goto("/register");
  await page.getByTestId("register-email-input").fill(email);
  await page.getByTestId("register-password-input").fill(password);

  await page.getByTestId("register-confirm-password-input").fill(password);
  await check("register");
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("register-success")).toBeVisible();

  const verifyLink = await waitForLink(
    email,
    /https:\/\/localhost\/[a-z]{2}\/verify-email\?token=[\w-]+/,
  );
  await page.goto(verifyLink);
  await expect(page.getByTestId("verify-email-success")).toBeVisible();
  await page.getByTestId("verify-email-continue-to-login").click();

  await page.getByTestId("login-email-input").fill(email);
  await page.getByTestId("login-password-input").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("create-first-org-prompt")).toBeVisible();
  await page.getByTestId("create-org-name-input").fill(`Targets Co ${runId}`);
  await page.getByTestId("create-org-submit").click();
  await expect(page.getByTestId("app-shell")).toBeVisible();

  await check("dashboard");

  // The palette is a list of targets and nothing else, and it is the surface
  // most likely to be operated on a phone.
  await page.getByTestId("palette-trigger").click();
  await expect(page.getByTestId("palette-nav-/")).toBeVisible();
  await check("command palette");
  await page.keyboard.press("Escape");

  await page.goto("/en/settings/profile");
  await expect(page.getByTestId("theme-toggle")).toBeVisible();
  await check("settings / profile");

  await page.goto("/en/settings/members");
  await expect(page.getByTestId("app-shell")).toBeVisible();
  await check("settings / members");

  // A dialog brings its own close button, which is the control this check was
  // written after: a bare 16x16 glyph until F-014 gave it a 40x40 hit area.
  await page.goto("/en/settings/security");
  await expect(page.getByTestId("app-shell")).toBeVisible();
  await check("settings / security");
});

test("targets hold up at phone width", async ({ page }) => {
  // 2.5.8 is measured in CSS pixels, so a narrower viewport does not change
  // the threshold -- but it does change which controls render (the nav
  // becomes a drawer) and how much they are squeezed by their neighbours.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/login");
  await expect(page.getByTestId("login-email-input")).toBeVisible();

  const undersized = await undersizedTargets(page);
  expect(
    undersized,
    `login at 375px has targets under ${MIN_TARGET_PX}x${MIN_TARGET_PX}:\n${describe(undersized)}`,
  ).toEqual([]);
});
