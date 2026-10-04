import { forwardRef } from "react";
import type { ButtonHTMLAttributes } from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@forge/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      // Disabled fills use the sunken surface rather than a faded accent, so a
      // disabled primary reads as unavailable, not as a broken brand colour.
      variant: {
        default:
          "bg-primary text-primary-foreground shadow-control hover:bg-primary/90 disabled:bg-surface-sunken disabled:text-muted-foreground disabled:shadow-none",
        destructive:
          "bg-destructive text-destructive-foreground shadow-control hover:bg-destructive/90 disabled:bg-surface-sunken disabled:text-muted-foreground disabled:shadow-none",
        outline:
          "bg-card text-foreground shadow-[0_0_0_1px_var(--color-border-strong)] hover:bg-surface-sunken disabled:bg-surface-sunken disabled:text-muted-foreground disabled:shadow-none",
        secondary:
          "bg-surface-sunken text-foreground hover:bg-surface-sunken/70 disabled:text-muted-foreground",
        ghost:
          "text-foreground-secondary hover:bg-surface-sunken hover:text-foreground disabled:text-muted-foreground",
        link: "text-primary underline decoration-primary/35 underline-offset-[3px] hover:decoration-current disabled:text-muted-foreground",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 px-3 text-xs",
        lg: "h-10 px-6",
        icon: "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends
    ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
