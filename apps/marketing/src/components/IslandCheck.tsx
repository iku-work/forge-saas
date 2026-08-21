/**
 * The island in ADR-0004's constraint 1, made executable (T-300).
 *
 * The claim the ADR settles on is that @forge/ui -- React 18 + Radix, every
 * component client-only by construction, and carrying no "use client"
 * directives -- can be hydrated by Astro exactly as it ships, with no
 * directives added to the package and no wrapper layer between it and this
 * app. A claim that nothing exercises decays; this component is what makes
 * the build fail if it stops being true.
 *
 * The state is not decoration: a <Button> that renders is only proof that
 * Astro can render React to HTML, which it does on the landing page without
 * hydrating anything. A counter that increments is proof the island got a
 * React root in the browser.
 */
import { useState } from "react";

import { Button } from "@forge/ui";

export interface IslandCheckProps {
  /** Translated by the page, at build time -- islands get finished strings, not an i18n provider. */
  label: string;
  countLabel: string;
}

export function IslandCheck({ label, countLabel }: IslandCheckProps) {
  const [count, setCount] = useState(0);

  return (
    <div className="flex items-center gap-4">
      <Button onClick={() => setCount((current) => current + 1)}>
        {label}
      </Button>
      <p className="text-sm text-muted-foreground" data-testid="island-count">
        {countLabel}: {count}
      </p>
    </div>
  );
}
