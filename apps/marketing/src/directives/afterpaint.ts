/**
 * `client:afterpaint` -- hydrate once the browser reports the first paint.
 *
 * Astro's own lazy directives all race the first paint. Measured on
 * /en/pricing/ in Lighthouse 12.6: `client:visible` and `client:idle` both
 * fire after layout and *before* paint, so the island's chunks are requested
 * while the Largest Contentful Paint is still pending. Lighthouse's simulated
 * LCP then counts every request that started before the observed one, and
 * whether react-dom (137 kB) made it in was a coin flip: CI measured 2.7 s,
 * 1.5 s and 2.7 s on the same build against a 2.5 s budget.
 *
 * requestAnimationFrame + setTimeout is not enough either: it runs after the
 * main thread paints but before the frame is presented, and the presentation
 * time is what LCP records. The `paint` entry is only delivered once that has
 * happened. On /en/pricing/ the LCP element (the subtitle) is in that first
 * frame, so waiting for it waits for LCP.
 */
import type { ClientDirective } from "astro";

const afterPaint: ClientDirective = (load) => {
  const hydrate = async () => {
    const run = await load();
    await run();
  };
  if (!PerformanceObserver.supportedEntryTypes.includes("paint")) {
    // No paint timing to wait for: next task after the next frame instead.
    requestAnimationFrame(() => setTimeout(() => void hydrate()));
    return;
  }
  const observer = new PerformanceObserver((list) => {
    if (list.getEntriesByName("first-contentful-paint").length === 0) {
      return;
    }
    observer.disconnect();
    void hydrate();
  });
  observer.observe({ type: "paint", buffered: true });
};

export default afterPaint;
