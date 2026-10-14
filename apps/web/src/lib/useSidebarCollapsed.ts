import { useCallback, useState } from "react";

/**
 * Whether the sidebar is collapsed to an icon rail -- per device, not per
 * account. A laptop and a wide monitor want different answers, so this lives
 * in the browser and never reaches the backend (decided for the visual
 * overhaul, ticket 05).
 */
export const SIDEBAR_STORAGE_KEY = "forge.sidebar";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "collapsed";
  } catch {
    // Private mode, or site data blocked: start expanded, and keep working.
    return false;
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(
      SIDEBAR_STORAGE_KEY,
      collapsed ? "collapsed" : "expanded",
    );
  } catch {
    // As above -- the choice still holds for this page's lifetime.
  }
}

export function useSidebarCollapsed(): {
  collapsed: boolean;
  toggle: () => void;
} {
  // Read in the initialiser, so the first render already has the stored
  // value and an expanded sidebar never flashes before collapsing.
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggle = useCallback(() => {
    const next = !collapsed;
    writeCollapsed(next);
    setCollapsed(next);
  }, [collapsed]);
  return { collapsed, toggle };
}
