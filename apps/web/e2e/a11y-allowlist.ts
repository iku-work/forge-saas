// T-221: rule/selector pairs explicitly accepted as known, pre-existing
// violations that can't be fixed immediately. Every entry needs a dated
// `// a11y-ok:` justification directly above it -- an empty array is the
// goal, and it should stay empty unless a real violation shows up that
// genuinely can't be fixed before the next PR.
export interface A11yAllowlistEntry {
  // axe-core rule id, e.g. "color-contrast".
  rule: string;
  // CSS selector as reported in the violation node's `target`.
  selector: string;
}

export const A11Y_ALLOWLIST: A11yAllowlistEntry[] = [];
