import { useEffect, useSyncExternalStore } from "react";

export interface PaletteCommand {
  /** Stable across renders -- cmdk keys rows on it, and registration replaces
   *  by id rather than appending, so a re-render cannot duplicate a row. */
  id: string;
  /** Already translated. A command can come from a module with its own i18n
   *  namespace, so the registry does not assume a key. */
  label: string;
  /** Extra words this row should match on that are not in the label
   *  ("logout" for "Sign out", say). cmdk searches label + value; this is
   *  appended to the value. */
  keywords?: string;
  group: "actions";
  run: () => void;
}

const registered = new Map<string, PaletteCommand[]>();
const listeners = new Set<() => void>();

// Recomputed on write rather than per read: useSyncExternalStore compares
// snapshots by identity and calls getSnapshot on every render, so building a
// fresh array there would loop forever.
let snapshot: PaletteCommand[] = [];

function recompute(): void {
  snapshot = [...registered.values()].flat();
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): PaletteCommand[] {
  return snapshot;
}

/**
 * Lets any feature add rows to the palette for as long as it is mounted
 * (F-005's "modules register their own commands").
 *
 * Keyed by `ownerId` rather than by command id so unmounting removes exactly
 * what that caller contributed, without it having to track its own ids.
 *
 * `commands` is expected to be referentially stable (module scope, or
 * useMemo). An inline array would re-register on every render -- harmless,
 * since registration replaces rather than appends, but it would wake every
 * subscriber each time.
 */
export function useRegisterCommands(
  ownerId: string,
  commands: PaletteCommand[],
): void {
  useEffect(() => {
    registered.set(ownerId, commands);
    recompute();
    return () => {
      registered.delete(ownerId);
      recompute();
    };
  }, [ownerId, commands]);
}

export function useRegisteredCommands(): PaletteCommand[] {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** Test-only: the registry is module state, and vitest isolates modules per
 *  file but not per test. */
export function resetRegisteredCommands(): void {
  registered.clear();
  recompute();
}
