import { useEffect, useRef } from "react";

import {
  SEQUENCE_LEAD,
  SEQUENCE_SHORTCUTS,
  SEQUENCE_TIMEOUT_MS,
  shouldIgnoreTarget,
} from "./registry";

export interface GlobalShortcutHandlers {
  onCommandPalette: () => void;
  onShowShortcuts: () => void;
  /** Called with a nav path ("/", "/files", ...) for a `g`-sequence. The
   *  caller decides whether that destination is reachable. */
  onNavigate: (path: string) => void;
  /** `[`: collapse or expand the sidebar. Optional, so a surface without a
   *  sidebar can use the rest of the shortcuts. */
  onToggleSidebar?: () => void;
}

/**
 * One keydown listener for the whole app (F-005, F-012).
 *
 * One rather than several because order matters between them: the palette
 * hotkey has to win over the sequence tracker, and the sequence tracker has
 * to be reset by any key that is not part of a sequence. Separate listeners
 * would have to coordinate through shared state anyway.
 *
 * Registered on `document` in the capture phase for the palette chord only in
 * spirit -- it is a bubble-phase listener, because a component that
 * deliberately handles ⌘K itself (none today) should be able to stop it, and
 * because capture-phase listeners fire before React's synthetic system and
 * make ordering harder to reason about, not easier.
 *
 * The handlers are held in a ref so the listener is attached exactly once.
 * Passing them as effect dependencies would re-attach on every render of the
 * shell, since callers naturally pass inline arrows.
 */
export function useGlobalShortcuts(handlers: GlobalShortcutHandlers): void {
  const handlersRef = useRef(handlers);
  // In an effect, not during render: React Compiler's "cannot access refs
  // during render" rule is enforced here, and it is right to be -- a ref
  // written during render is not safe under concurrent rendering, where a
  // render can be thrown away. The listener only reads it from a real DOM
  // event, which is always after commit.
  useEffect(() => {
    handlersRef.current = handlers;
  }, [handlers]);

  // The pending `g`, and when it was pressed. A ref rather than state: nothing
  // renders from it, and a state update per keystroke would re-render the
  // entire shell while the user types.
  const pendingLead = useRef<number | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      // A dead chord: the user is holding the modifier down and this is the
      // modifier's own keydown. Nothing to match, and resetting the sequence
      // here would break nothing but is needless work.
      if (event.altKey) {
        return;
      }

      const mod = event.metaKey || event.ctrlKey;

      // ⌘K / Ctrl+K -- fires even from inside an input, see
      // shouldIgnoreTarget's docstring.
      if (mod && event.key.toLowerCase() === "k") {
        event.preventDefault();
        pendingLead.current = null;
        handlersRef.current.onCommandPalette();
        return;
      }

      // Everything below is a bare key, so typing must be left alone. Any
      // other modifier combination also clears a pending `g`: ⌘S in the
      // middle of a sequence means the user moved on.
      if (mod) {
        pendingLead.current = null;
        return;
      }
      if (shouldIgnoreTarget(event.target)) {
        return;
      }

      if (event.key === "?") {
        event.preventDefault();
        pendingLead.current = null;
        handlersRef.current.onShowShortcuts();
        return;
      }

      const onToggleSidebar = handlersRef.current.onToggleSidebar;
      if (event.key === "[" && onToggleSidebar) {
        event.preventDefault();
        pendingLead.current = null;
        onToggleSidebar();
        return;
      }

      const now = Date.now();
      const lead = pendingLead.current;
      if (lead !== null && now - lead <= SEQUENCE_TIMEOUT_MS) {
        const match = SEQUENCE_SHORTCUTS.find(
          (shortcut) => shortcut.second === event.key.toLowerCase(),
        );
        pendingLead.current = null;
        if (match) {
          event.preventDefault();
          handlersRef.current.onNavigate(match.path);
          return;
        }
      }

      // Start (or restart) a sequence. Any other bare key clears it, so a
      // stale `g` cannot combine with a letter pressed much later.
      pendingLead.current =
        event.key.toLowerCase() === SEQUENCE_LEAD ? now : null;
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);
}
