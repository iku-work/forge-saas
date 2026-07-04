import { useEffect, useState } from "react";

// Shared by the orgs/users/audit admin list pages -- typing into a search
// box fires a query per keystroke otherwise, hammering the admin API for
// every intermediate substring.
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebounced(value);
    }, delayMs);
    return () => {
      clearTimeout(timer);
    };
  }, [value, delayMs]);

  return debounced;
}
