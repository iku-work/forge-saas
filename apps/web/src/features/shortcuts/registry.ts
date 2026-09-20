/**
 * Every keyboard shortcut this app answers, declared once (F-012).
 *
 * The handler and the cheat sheet both read this list, which is the point: a
 * documented shortcut that does nothing, or a working one nobody can
 * discover, are the two failure modes of every shortcut system, and both come
 * from keeping the implementation and the documentation in separate places.
 *
 * `react-hotkeys-hook` (which the plan suggests) is deliberately not used.
 * The hard part here is not key matching -- it is deciding when *not* to fire
 * (see shouldIgnoreTarget below) and rendering the same list as help, neither
 * of which a matching library does for you. Every dependency added to this
 * repo also has to clear the security job's pnpm-audit gate, which is a real
 * recurring cost for ~80 lines of event handling.
 */

export type ShortcutCategory = "navigation" | "actions" | "view";

export interface Shortcut {
  id: string;
  /** Rendered in the cheat sheet, one token per key. `mod` is substituted for
   *  the platform's own modifier at render time. */
  keys: readonly string[];
  labelKey: string;
  category: ShortcutCategory;
}

/**
 * A `g`-then-letter sequence, in the Gmail tradition the plan names. Kept
 * apart from the chorded shortcuts because the matching is different: these
 * need the *previous* key, and they must never fire while the user is typing.
 */
export interface SequenceShortcut extends Shortcut {
  /** The second key, pressed within SEQUENCE_TIMEOUT_MS of `g`. */
  second: string;
  /** Which nav item to go to, by its unprefixed path. Resolved against the
   *  user's *visible* nav, so a shortcut never navigates to a page the
   *  modules or the user's role have turned off. */
  path: string;
}

export const SEQUENCE_LEAD = "g";
/** Long enough to be typed deliberately, short enough that a `g` typed a
 *  paragraph ago does not turn a later `d` into a navigation. */
export const SEQUENCE_TIMEOUT_MS = 1500;

export const SEQUENCE_SHORTCUTS: readonly SequenceShortcut[] = [
  {
    id: "goto-dashboard",
    keys: ["g", "d"],
    second: "d",
    path: "/",
    labelKey: "common.shortcuts.goto_dashboard",
    category: "navigation",
  },
  {
    id: "goto-files",
    keys: ["g", "f"],
    second: "f",
    path: "/files",
    labelKey: "common.shortcuts.goto_files",
    category: "navigation",
  },
  {
    id: "goto-settings",
    keys: ["g", "s"],
    second: "s",
    path: "/settings/profile",
    labelKey: "common.shortcuts.goto_settings",
    category: "navigation",
  },
];

export const CHORD_SHORTCUTS: readonly Shortcut[] = [
  {
    id: "command-palette",
    keys: ["mod", "K"],
    labelKey: "common.shortcuts.command_palette",
    category: "actions",
  },
  {
    id: "shortcuts-help",
    keys: ["?"],
    labelKey: "common.shortcuts.help",
    category: "actions",
  },
  {
    id: "close",
    keys: ["Esc"],
    labelKey: "common.shortcuts.close",
    category: "actions",
  },
];

export const ALL_SHORTCUTS: readonly Shortcut[] = [
  ...CHORD_SHORTCUTS,
  ...SEQUENCE_SHORTCUTS,
];

export const SHORTCUT_CATEGORIES: readonly ShortcutCategory[] = [
  "navigation",
  "actions",
  "view",
];

/** `true` on Apple platforms, where the palette hint reads ⌘K rather than
 *  Ctrl+K. Getting it wrong costs a wrong glyph in a hint, not a broken
 *  shortcut -- the handler accepts either modifier regardless.
 *
 *  `userAgent` rather than the more obvious `navigator.platform`, which is
 *  deprecated. An iPad reporting "Macintosh" (which recent ones do) still
 *  lands on the right answer here. */
export function isAppleDevice(): boolean {
  return /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent);
}

/** "mod" -> the platform's modifier glyph. */
export function renderKey(key: string, apple: boolean): string {
  if (key === "mod") {
    return apple ? "⌘" : "Ctrl";
  }
  return key;
}

/**
 * Whether a keydown should be left alone because the user is typing into
 * something.
 *
 * Chorded shortcuts (⌘K) pass through anyway -- a modifier combination cannot
 * be produced by ordinary typing, and a palette that refuses to open because
 * focus happens to sit in a search box is worse than useless. Bare keys (`?`,
 * `g d`) must not fire: typing "g" then "d" into a name field would otherwise
 * navigate away and lose the form.
 */
export function shouldIgnoreTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}
