import { forwardRef } from "react";
import type { HTMLAttributes, Ref } from "react";

import { cn } from "@forge/utils";

const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("rounded-xl border bg-card text-card-foreground shadow", className)}
      {...props}
    />
  ),
);
Card.displayName = "Card";

const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex flex-col space-y-1.5 p-6", className)} {...props} />
  ),
);
CardHeader.displayName = "CardHeader";

/**
 * The tags a card title may be rendered as.
 *
 * A card title looks like a heading and is almost never marked up as one --
 * this component rendered a `<div>` and nothing else until T-306, and the
 * marketing site's pricing page paid for it: three plan cards under an `<h1>`,
 * each with an `<h3>` inside, and no `<h2>` anywhere between them. That is a
 * skipped heading level, which axe and Lighthouse both fail, and which a
 * screen-reader user experiences as a list of level-3 headings belonging to
 * nothing.
 *
 * `<div>` stays the default deliberately. A card in a dialog, a card that is
 * one of forty in a grid, a card whose title repeats the page's -- those are
 * the majority, and a heading there adds noise to the document outline rather
 * than structure. The prop exists so that a card which *is* a section of the
 * page can say so, at the one place that already owns the styling.
 */
type CardTitleTag = "div" | "h1" | "h2" | "h3" | "h4" | "h5" | "h6";

interface CardTitleProps extends HTMLAttributes<HTMLDivElement> {
  /** Defaults to `"div"`; pass a heading when the card is a section of the page. */
  as?: CardTitleTag;
}

const CardTitle = forwardRef<HTMLDivElement, CardTitleProps>(
  ({ className, as: Tag = "div", ...props }, ref) => (
    <Tag
      // One component, seven possible tags, so the forwarded ref has to
      // satisfy every one of their element types at once. HTMLDivElement is
      // what callers who never pass `as` are handed back, which is every
      // caller that existed before the prop did.
      ref={ref as Ref<HTMLDivElement & HTMLHeadingElement>}
      className={cn("font-semibold leading-none tracking-tight", className)}
      {...props}
    />
  ),
);
CardTitle.displayName = "CardTitle";

const CardDescription = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
  ),
);
CardDescription.displayName = "CardDescription";

const CardContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />,
);
CardContent.displayName = "CardContent";

const CardFooter = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex items-center p-6 pt-0", className)} {...props} />
  ),
);
CardFooter.displayName = "CardFooter";

export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent };
