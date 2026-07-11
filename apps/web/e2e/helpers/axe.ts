// T-221: shared axe-core scan used by a11y.spec.ts against every key page.
// Serious/critical violations fail the run; moderate-and-below are recorded
// as annotations on the test result instead of failing it, per this task's
// own severity split.
import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";
import { expect } from "@playwright/test";

import { A11Y_ALLOWLIST } from "../a11y-allowlist";

const BLOCKING_IMPACTS = new Set(["serious", "critical"]);

// axe-core scopes WCAG 2.1 A/AA (the "wcag2a"/"wcag2aa" tags predate 2.1 and
// only cover the rules that existed at 2.0; wcag21a/aa layer in the rules
// 2.1 added) -- this task's own goal is "WCAG A/AA violations fail the
// build", so both generations of tags are included.
const WCAG_A_AA_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

// Scans the page as it currently stands (caller navigates/sets up state
// first) and fails the test if any serious/critical violation isn't covered
// by a11y-allowlist.ts. `label` identifies the page in the failure message
// and in the moderate-violation annotations -- axe-core's own violation
// objects don't know what page they ran against.
export async function checkA11y(page: Page, testInfo: TestInfo, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_A_AA_TAGS).analyze();

  const blocking: string[] = [];
  for (const violation of results.violations) {
    const allowedSelectors = new Set(
      A11Y_ALLOWLIST.filter((entry) => entry.rule === violation.id).map((entry) => entry.selector),
    );
    const uncoveredNodes = violation.nodes.filter(
      (node) => !node.target.some((target) => allowedSelectors.has(String(target))),
    );
    if (uncoveredNodes.length === 0) continue;

    const targets = uncoveredNodes.map((node) => node.target.join(" ")).join(", ");
    const description = `[${label}] ${violation.id} (${violation.impact ?? "unknown"}): ${violation.help} -- ${targets}`;

    if (violation.impact && BLOCKING_IMPACTS.has(violation.impact)) {
      blocking.push(description);
    } else {
      testInfo.annotations.push({ type: "a11y-violation", description });
    }
  }

  expect(blocking, blocking.join("\n")).toEqual([]);
}
