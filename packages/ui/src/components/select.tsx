import { forwardRef } from "react";
import type { ComponentProps } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@forge/utils";

/**
 * A native <select>, styled like Input.
 *
 * Native rather than a custom popup: it is the accessible, phone-friendly
 * control every browser already gets right, and none of the places that
 * pick one value from a short list needs more. `className` sizes the
 * control (it lands on the wrapper that positions the chevron); every other
 * prop -- including the id and aria attributes a form's FormControl passes
 * down -- goes to the <select> itself.
 */
const Select = forwardRef<HTMLSelectElement, ComponentProps<"select">>(
  ({ className, children, ...props }, ref) => (
    <div className={cn("relative w-full", className)}>
      <select
        ref={ref}
        className="flex h-9 w-full appearance-none rounded-md border border-input bg-card pe-9 ps-3 text-sm transition-colors hover:border-muted-foreground focus-visible:border-primary focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute end-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
    </div>
  ),
);
Select.displayName = "Select";

export { Select };
