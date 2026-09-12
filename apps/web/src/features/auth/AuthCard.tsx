import { useEffect, useRef, type ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader } from "@forge/ui";

interface AuthCardProps {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}

export function AuthCard({
  title,
  description,
  children,
  footer,
}: AuthCardProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  // Re-focus the heading whenever the title changes -- on the initial
  // navigation into a page, and on every in-page async-state transition
  // (e.g. "Verifying your email…" -> "Email verified") so screen reader
  // users land on the new content instead of keeping focus on a now-gone
  // button.
  useEffect(() => {
    headingRef.current?.focus();
  }, [title]);

  return (
    // `m-auto` on the card rather than `items-center` on the flex parent.
    // They centre identically until the card is taller than the viewport --
    // which happens on a landscape phone, or any time the on-screen keyboard
    // halves the height. At that point `items-center` overflows the box in
    // *both* directions and the top of the card, including its heading, is
    // unreachable: scrolling only ever reveals the bottom. Auto margins
    // collapse to zero instead of going negative, so the card pins to the top
    // and the whole of it can be scrolled to.
    <div className="flex min-h-dvh p-4">
      <Card className="m-auto w-full max-w-sm">
        <CardHeader>
          {/* CardTitle renders a <div>, not a heading -- each auth page needs
              a real <h1> for a11y and for the page's document outline. */}
          <h1
            ref={headingRef}
            tabIndex={-1}
            className="text-xl font-semibold leading-none tracking-tight focus:outline-none"
          >
            {title}
          </h1>
          {description ? (
            <CardDescription>{description}</CardDescription>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4">
          {children}
          {footer ? (
            <div className="space-y-2 text-center text-sm text-muted-foreground">
              {footer}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
