/**
 * A component that has no business being a component (T-306).
 *
 * The island rule this site is built on -- ADR-0004, and enforced by review --
 * says a page may only hydrate something that cannot be expressed as markup.
 * A pricing toggle, a mobile nav, an accordion: yes. A list of features, a
 * heading, a paragraph: no, those are HTML. This file is the "no" case,
 * written out so that lighthouserc.fixture.cjs has something real to fail on.
 *
 * It lives under tests/fixtures/ rather than under src/, and the sibling
 * unnecessary-island.astro says why: Vite bundles what a page *imports*, not
 * what it renders, so an island reachable from src/pages/ ships a JavaScript
 * chunk to production even when no page ever renders it. Nothing imports this
 * file until scripts/lighthouse-fixture.mjs injects that route, which it does
 * only when FORGE_LIGHTHOUSE_FIXTURE is set.
 *
 * **The cost below is exaggerated on purpose.** A genuinely unnecessary island
 * of this size costs a CI runner some tens of milliseconds -- enough to matter
 * once a page has four of them, not enough to fail a 200 ms budget on its own,
 * and *nowhere near* deterministic on a shared runner of unknown speed. A
 * regression fixture that fails 70% of the time is worse than none: it teaches
 * everyone to re-run the job. So this one blocks the main thread for a fixed,
 * unmissable interval, and the honest reading of it is "this is what the gate
 * measures", not "this is what one island costs".
 */
import { useState } from "react";

/**
 * Main-thread work, in milliseconds.
 *
 * Six times the 200 ms budget, so the assertion fails on a fast laptop and on
 * a throttled CI runner alike, and the failure names total-blocking-time
 * rather than landing just over the line on some other metric.
 */
const BLOCKING_MS = 1200;

function burnMainThread(milliseconds: number): void {
  const until = Date.now() + milliseconds;
  // A spin rather than a sleep: the point is to occupy the main thread, which
  // is what a hydrating island does and what Total Blocking Time counts. A
  // timer would yield, and Lighthouse would correctly report no blocking.
  while (Date.now() < until) {
    /* deliberately empty */
  }
}

export default function UnnecessaryIsland({
  heading,
  items,
}: {
  heading: string;
  items: string[];
}) {
  // Server-rendered too (Astro renders an island's initial HTML at build
  // time), and there the cost is just a slower build for no reason -- so it
  // is skipped. `typeof window` rather than an `import.meta.env` check: this
  // has to be false during the build and true in the browser, which is
  // exactly what that expression means and is not what PROD/DEV mean.
  if (typeof window !== "undefined") {
    // In the browser this runs during hydration, which happens after the
    // server-rendered HTML has painted -- so it lands inside the window
    // Total Blocking Time measures (first contentful paint onwards) rather
    // than before it, where it would delay paint and be counted as something
    // else entirely.
    burnMainThread(BLOCKING_MS);
  }

  // State exists only so that this is not trivially provable as static markup
  // by a future reviewer looking for a reason to delete the island. Nothing
  // sets it, which is the joke: the page never needed React at all.
  const [rendered] = useState(() => new Date().getFullYear());

  return (
    <section className="mx-auto max-w-3xl px-6 py-16">
      <h2 className="text-2xl font-semibold tracking-tight">{heading}</h2>
      <ul className="mt-6 space-y-2 text-muted-foreground">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p className="mt-6 text-sm text-muted-foreground">{rendered}</p>
    </section>
  );
}
