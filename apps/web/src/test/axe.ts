import { run } from "axe-core";
import { expect } from "vitest";

export async function expectNoA11yViolations(
  container: Element,
): Promise<void> {
  const results = await run(container, {
    // jsdom has no layout engine, so color-contrast can't be computed and
    // region/landmark rules expect a full page shell these isolated page
    // renders don't provide.
    rules: {
      "color-contrast": { enabled: false },
      region: { enabled: false },
    },
  });
  expect(results.violations).toEqual([]);
}
