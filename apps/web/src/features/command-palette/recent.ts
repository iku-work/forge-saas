const STORAGE_KEY = "forge.recent-pages";
/** Five, per F-005. Enough to cover a back-and-forth between two or three
 *  pages; short enough that the group never pushes the rest of the palette
 *  below the fold. */
const MAX_RECENT = 5;

/**
 * The last few unprefixed nav paths the user visited ("/files",
 * "/settings/org"), most recent first.
 *
 * Paths only -- never labels. A label is a translation that can change with
 * the locale, and a stored one would show the previous language's wording
 * after a switch. The palette resolves each path against the current nav.
 */
export function readRecentPaths(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .filter((entry): entry is string => typeof entry === "string")
      .slice(0, MAX_RECENT);
  } catch {
    // Unreadable storage, or someone hand-edited the value into
    // non-JSON. Recents are a convenience; losing them is not an error.
    return [];
  }
}

export function recordRecentPath(path: string): void {
  try {
    const next = [path, ...readRecentPaths().filter((p) => p !== path)].slice(
      0,
      MAX_RECENT,
    );
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // As above.
  }
}
