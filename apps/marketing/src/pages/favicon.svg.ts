import type { APIRoute } from "astro";

import { site } from "../config/site";

/**
 * The favicon, drawn from `primaryColor` rather than committed as an asset.
 *
 * A committed .svg would be the second place the brand colour is written
 * down, and the one nobody remembers to change -- the drift this task's
 * shared token layer exists to prevent, just in a file Tailwind never sees.
 * An endpoint costs nothing in a static build: it is written once, to
 * dist/favicon.svg, at build time.
 */
// The product name reaches both an attribute and a text node below, and it
// is arbitrary text from product.config.ts -- an apostrophe in "Acme's" is
// enough to produce a document no parser accepts.
function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export const GET: APIRoute = () => {
  const initial = escapeXml(site.name.trim().charAt(0).toUpperCase());
  const label = escapeXml(site.name);
  // An intrinsic size as well as a viewBox: this file is also the
  // `logo` of T-303's Organization node, and Google's logo guidance asks for
  // at least 112x112. A viewBox alone leaves the intrinsic size at the
  // viewport's 32x32, which is below that; a browser drawing a favicon scales
  // by the viewBox either way, so nothing about the icon changes.
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 32 32" role="img" aria-label="${label}">
  <rect width="32" height="32" rx="7" fill="${site.brandColor}"/>
  <text x="16" y="22" text-anchor="middle" font-family="system-ui, sans-serif" font-size="18" font-weight="600" fill="#ffffff">${initial}</text>
</svg>
`;
  return new Response(svg, {
    headers: {
      "Content-Type": "image/svg+xml",
      "Cache-Control": "public, max-age=86400",
    },
  });
};
