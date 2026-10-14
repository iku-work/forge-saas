import { useEffect, useState } from "react";

// Shared by the admin list pages and the members search -- typing into a search
// box fires a query per keystroke otherwise, hammering the API for
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
