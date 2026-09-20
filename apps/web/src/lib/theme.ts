/**
 * The theme store (F-004).
 *
 * Three concerns kept apart on purpose:
 *
 *   - the *preference*, which is what the user chose and what gets persisted
 *     ("light" | "dark" | "system");
 *   - the *resolved* theme, which is what is actually painted ("light" |
 *     "dark") -- "system" resolves through `prefers-color-scheme`;
 *   - *applying* it, which is one class on <html>.
 *
 * Why resolution happens here in JS rather than in a CSS media query:
 * packages/ui's components select dark styling with Tailwind's `dark:`
 * variant, which is compiled to `&:is(.dark *)` -- a class, not a media
 * query. If the token layer *also* flipped on `prefers-color-scheme`, the
 * variables and the component classes could disagree (dark surfaces, light
 * component internals) on any machine whose OS preference contradicts the
 * class. One mechanism, one source of truth: the class.
 *
 * The cost of that choice is a flash of the light theme before the bundle
 * runs, which is paid off by the synchronous boot script in index.html --
 * see THEME_BOOT_SNIPPET at the bottom, which must stay in step with this
 * file.
 */

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_PREFERENCES: readonly ThemePreference[] = [
  "light",
  "dark",
  "system",
];

/** Shared with the boot snippet below. Changing it orphans every existing
 *  user's saved choice, which reads as "the toggle forgot me". */
export const THEME_STORAGE_KEY = "forge.theme";

/** Named channel so a second tab hears a change immediately rather than on
 *  its next reload. A `storage` event would also fire cross-tab, but only for
 *  a localStorage write -- this also carries a preference that came from the
 *  *server* (another device's choice, arriving via /auth/me). */
const THEME_CHANNEL = "forge.theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function isThemePreference(value: unknown): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

export function readStoredPreference(): ThemePreference {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(raw) ? raw : "system";
  } catch {
    // Private mode, or site data blocked. The preference simply does not
    // survive this reload; it must not take the app down with it.
    return "system";
  }
}

function writeStoredPreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // As above -- the theme still applies for this page's lifetime.
  }
}

/**
 * `prefers-color-scheme: dark`, or null where the environment has no
 * `matchMedia` -- which in practice means jsdom, where most of this app's
 * tests run and where this module is imported by the app shell.
 *
 * The local annotation is what makes the guard honest: lib.dom types
 * `window.matchMedia` as always present, so without it
 * `@typescript-eslint/no-unnecessary-condition` reads the check as dead code
 * and fails the build -- for a call that genuinely throws in jsdom.
 */
function darkMediaQuery(): MediaQueryList | null {
  try {
    return window.matchMedia(DARK_QUERY);
  } catch {
    // try/catch rather than an existence check, because the absence is not
    // expressible as one: lib.dom declares `matchMedia` as required on
    // Window, so every `?.` or truthiness guard reads as dead code to
    // `@typescript-eslint/no-unnecessary-condition`, and re-typing it as
    // optional does not survive TypeScript's narrowing of a `const` to its
    // initialiser. The real failure mode is a thrown TypeError anyway --
    // jsdom, where most of this app's tests run, has no `matchMedia` at all
    // -- so catching is both the honest description and the shorter code.
    return null;
  }
}

export function systemTheme(): ResolvedTheme {
  return darkMediaQuery()?.matches ? "dark" : "light";
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? systemTheme() : preference;
}

export function applyTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  // Tells the UA which built-ins to paint dark: form controls, scrollbars,
  // the canvas behind an over-scroll. Without it a dark page keeps a white
  // scrollbar and light-on-light date pickers. Set as an inline style rather
  // than in the stylesheet so it is already correct when the boot snippet
  // runs, before any stylesheet has loaded.
  root.style.colorScheme = resolved;
}

// --- the store ------------------------------------------------------------

let preference: ThemePreference = "system";
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

/** Set the preference *locally*: storage, the DOM, other tabs. Persisting it
 *  to the account is the caller's job (useTheme), because not every caller
 *  has one -- the cross-tab listener below is applying a choice the tab that
 *  made it has already persisted. */
function setLocalPreference(next: ThemePreference, broadcast: boolean): void {
  if (preference === next) {
    // Still re-apply: a "system" preference can resolve differently now even
    // though the preference itself has not changed.
    applyTheme(resolveTheme(next));
    return;
  }
  preference = next;
  writeStoredPreference(next);
  applyTheme(resolveTheme(next));
  if (broadcast) {
    postPreference(next);
  }
  emit();
}

export function setPreference(next: ThemePreference): void {
  setLocalPreference(next, true);
}

/** Adopt a preference that arrived from the server (the signed-in user's
 *  saved choice). Deliberately does NOT broadcast: every tab gets the same
 *  value from its own /auth/me, so broadcasting would have each tab tell the
 *  others what they already know. */
export function adoptServerPreference(next: ThemePreference): void {
  setLocalPreference(next, false);
}

export function getPreference(): ThemePreference {
  return preference;
}

export function getResolvedTheme(): ResolvedTheme {
  return resolveTheme(preference);
}

let channel: BroadcastChannel | null = null;

function openChannel(): BroadcastChannel | null {
  try {
    channel ??= new BroadcastChannel(THEME_CHANNEL);
  } catch {
    // Some embedded webviews. Cross-tab sync is the feature that degrades,
    // not the theme.
    channel = null;
  }
  return channel;
}

function postPreference(next: ThemePreference): void {
  openChannel()?.postMessage(next);
}

/**
 * Wires the store to the two things that can change it from outside React:
 * the OS preference, and another tab. Returns a teardown.
 *
 * Called once from the app root rather than per-hook -- every `useTheme()`
 * subscribing its own matchMedia listener would be N listeners doing
 * identical work, and `useSyncExternalStore` already fans one store update
 * out to all of them.
 */
export function startThemeSync(): () => void {
  preference = readStoredPreference();
  applyTheme(resolveTheme(preference));

  const media = darkMediaQuery();
  const onSystemChange = (): void => {
    // Only "system" follows the OS. A pinned light/dark preference stays
    // pinned when the OS flips at sunset, which is the point of pinning it.
    if (preference === "system") {
      applyTheme(systemTheme());
      emit();
    }
  };
  media?.addEventListener("change", onSystemChange);

  const onMessage = (event: MessageEvent<unknown>): void => {
    if (isThemePreference(event.data)) {
      setLocalPreference(event.data, false);
    }
  };
  const openedChannel = openChannel();
  openedChannel?.addEventListener("message", onMessage);

  return () => {
    media?.removeEventListener("change", onSystemChange);
    openedChannel?.removeEventListener("message", onMessage);
  };
}

export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * The boot script, as a string, so the place it is inlined
 * (apps/web/index.html) and the place it is tested (lib/theme.test.ts) cannot
 * drift apart.
 *
 * Runs synchronously in <head>, before the stylesheet paints anything, which
 * is the only way to avoid a flash of the wrong theme: a `type="module"`
 * script is deferred by definition, so main.tsx is already too late.
 *
 * Deliberately duplicates a few lines of the logic above rather than
 * importing it -- an import would make it a module, and a module is deferred.
 * Wrapped in try/catch in full: a theme is not worth a blank page.
 */
export const THEME_BOOT_SNIPPET = [
  "try {",
  `  var p = localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});`,
  '  if (p !== "light" && p !== "dark") p = "system";',
  `  var d = p === "dark" || (p === "system" && matchMedia(${JSON.stringify(DARK_QUERY)}).matches);`,
  '  document.documentElement.classList.toggle("dark", d);',
  '  document.documentElement.style.colorScheme = d ? "dark" : "light";',
  "} catch (e) {}",
].join("\n");
