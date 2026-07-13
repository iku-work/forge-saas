// T-224: shared stabilization for visual.spec.ts's toHaveScreenshot() calls.
// test.use({ reducedMotion: "reduce" }) in visual.spec.ts already flips the
// prefers-reduced-motion media feature, which covers every Radix-driven
// open/close transition in this app (see a11y.spec.ts's notification-center
// comment for the one case that needed an explicit wait instead) -- the
// style tag below is a second line of defense that kills animations and
// transitions unconditionally, so a future component that doesn't bother
// checking the media feature can't reintroduce a flaky capture. Waiting on
// document.fonts.ready avoids a race where a late web-font swap shifts text
// metrics mid-capture.
import type { Page } from "@playwright/test";

export async function stabilizeForSnapshot(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation-duration: 0s !important;
        animation-delay: 0s !important;
        transition-duration: 0s !important;
        transition-delay: 0s !important;
        caret-color: transparent !important;
      }
    `,
  });
}
