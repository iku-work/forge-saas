import { useEffect, useState } from "react";
import { render } from "react-email";
import type { ReactElement } from "react";

/**
 * T-150: every template renders a full `<Html><Head><Body>...` document
 * (BaseLayout.tsx) -- mounting that tree directly into Storybook's own
 * page would nest a second html/head/body inside the host document, which
 * browsers don't parse consistently. Rendering to a static HTML string via
 * react-email's own `render()` (the same call `renderEmail()` in
 * ../render.ts makes for real sends) and displaying it in an iframe is what
 * every email client and react-email's own dev server do for exactly this
 * reason.
 */
export function EmailPreview({ element }: { element: ReactElement }) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void render(element, { pretty: false }).then((result) => {
      if (!cancelled) setHtml(result);
    });
    return () => {
      cancelled = true;
    };
  }, [element]);

  if (html === null) {
    return null;
  }

  return (
    <iframe
      title="Email preview"
      srcDoc={html}
      style={{
        width: "100%",
        height: "720px",
        border: "1px solid #e5e7eb",
        borderRadius: 8,
      }}
    />
  );
}
