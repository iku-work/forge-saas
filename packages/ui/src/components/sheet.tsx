import { forwardRef } from "react";
import type { ComponentPropsWithoutRef, ElementRef } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { cn } from "@forge/utils";

/**
 * A panel that slides in from an edge -- the app shell's navigation on a
 * phone.
 *
 * Built on Radix's Dialog rather than a `translate-x` div with a boolean,
 * because an off-canvas nav has to do everything a modal does: trap focus
 * while it is open, close on Escape, restore focus to the trigger, mark the
 * rest of the page `aria-hidden`, and stop the body scrolling underneath.
 * Hand-rolling that is how a drawer ends up keyboard-inaccessible, which
 * apps/web/e2e/a11y.spec.ts would fail on -- and rightly.
 *
 * `@radix-ui/react-dialog` is already a dependency of this package, so this
 * costs no new package.
 *
 * Sides are logical (`start`/`end`), not physical: the shell's navigation
 * lives on the inline-start edge, which is the right-hand side under the `ar`
 * locale (T-231). A physical left-edge class here would put the drawer on the
 * wrong side of an rtl page, and the rtl visual baseline would catch it.
 */
const Sheet = DialogPrimitive.Root;
const SheetTrigger = DialogPrimitive.Trigger;
const SheetClose = DialogPrimitive.Close;
const SheetPortal = DialogPrimitive.Portal;

const SheetOverlay = forwardRef<
  ElementRef<typeof DialogPrimitive.Overlay>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-50 bg-black/30 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className,
    )}
    {...props}
  />
));
SheetOverlay.displayName = DialogPrimitive.Overlay.displayName;

interface SheetContentProps extends ComponentPropsWithoutRef<
  typeof DialogPrimitive.Content
> {
  /** Which inline edge the panel is anchored to. Defaults to `start`. */
  side?: "start" | "end";
}

const SheetContent = forwardRef<
  ElementRef<typeof DialogPrimitive.Content>,
  SheetContentProps
>(({ className, children, side = "start", ...props }, ref) => (
  <SheetPortal>
    <SheetOverlay />
    <DialogPrimitive.Content
      ref={ref}
      // A stable hook for e2e: apps/web/e2e is data-testid-only (CI greps for
      // any other selector and fails), and this panel has no other one. A
      // caller can still override it via props.
      data-testid="sheet-content"
      className={cn(
        // h-dvh, not h-screen: on a phone `100vh` is the viewport with the
        // browser chrome *hidden*, so a full-height panel is taller than what
        // you can see and its last item sits under the toolbar.
        // `inset-y-0` and `start-0`/`end-0`, NOT `inset-block-0` and
        // `inset-inline-start-0`: Tailwind v4 *already* compiles the former
        // to the logical properties (inset-block, inset-inline-start), and
        // the CSS-property spellings are not utilities at all. Writing them
        // out produced no rule -- so this panel was `fixed` with no anchor,
        // landing at its static position at the end of the portal, below the
        // fold, while the overlay covered the screen. On a phone that read
        // as "the menu does not open and everything turns blue". Nothing
        // errors on an unknown Tailwind class, so only the built CSS shows
        // it; sheet.test.tsx now asserts these class names.
        "fixed inset-y-0 z-50 flex h-dvh w-72 max-w-[85vw] flex-col gap-0 overflow-y-auto bg-card p-4 shadow-menu transition ease-in-out data-[state=closed]:duration-200 data-[state=open]:duration-300 data-[state=open]:animate-in data-[state=closed]:animate-out",
        side === "start"
          ? "start-0 data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left"
          : "end-0 data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
        className,
      )}
      {...props}
    >
      {children}
      {/* F-014: see dialog.tsx -- same 16x16 glyph, same 40x40 hit area. */}
      <DialogPrimitive.Close className="absolute end-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity after:absolute after:-inset-3 after:content-[''] hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none">
        <X className="size-4" aria-hidden="true" />
        <span className="sr-only">Close</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </SheetPortal>
));
SheetContent.displayName = DialogPrimitive.Content.displayName;

const SheetTitle = forwardRef<
  ElementRef<typeof DialogPrimitive.Title>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("text-body font-semibold text-foreground", className)}
    {...props}
  />
));
SheetTitle.displayName = DialogPrimitive.Title.displayName;

const SheetDescription = forwardRef<
  ElementRef<typeof DialogPrimitive.Description>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-body-sm text-muted-foreground", className)}
    {...props}
  />
));
SheetDescription.displayName = DialogPrimitive.Description.displayName;

export {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetOverlay,
  SheetPortal,
  SheetTitle,
  SheetTrigger,
};
