import { forwardRef } from "react";
import type {
  ComponentPropsWithoutRef,
  ElementRef,
  HTMLAttributes,
} from "react";
import { Command as CommandPrimitive } from "cmdk";
import { Search } from "lucide-react";

import { cn } from "@forge/utils";

import { Dialog, DialogContent, DialogTitle } from "./dialog";

/**
 * The command-palette primitives (F-005), over `cmdk`.
 *
 * cmdk owns the parts that are genuinely hard: the filter/score pass, the
 * roving `aria-activedescendant` (the listbox keeps DOM focus on the input
 * while the highlighted row is announced), and keeping the selected item
 * scrolled into view. What is added here is this system's own chrome and one
 * accessibility fix cmdk does not make for you -- see CommandDialog.
 */
const Command = forwardRef<
  ElementRef<typeof CommandPrimitive>,
  ComponentPropsWithoutRef<typeof CommandPrimitive>
>(({ className, ...props }, ref) => (
  <CommandPrimitive
    ref={ref}
    className={cn(
      "flex size-full flex-col overflow-hidden rounded-lg bg-popover text-popover-foreground",
      className,
    )}
    {...props}
  />
));
Command.displayName = CommandPrimitive.displayName;

interface CommandDialogProps extends ComponentPropsWithoutRef<typeof Dialog> {
  /** Names the dialog for assistive technology. Required, not optional with a
   *  default: Radix warns at runtime without one, and a palette whose only
   *  visible text is a placeholder has nothing else to be named by. */
  label: string;
  className?: string;
  children: React.ReactNode;
}

/**
 * The palette as a modal.
 *
 * `top-[15%]` rather than vertically centred: a list that grows downward from
 * a fixed point does not shift the input under the user's cursor as results
 * filter, which centring does on every keystroke.
 */
const CommandDialog = ({
  label,
  className,
  children,
  ...props
}: CommandDialogProps) => (
  <Dialog {...props}>
    <DialogContent
      className={cn(
        "top-[15%] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl [&>button]:hidden",
        className,
      )}
      // The palette *is* its own description -- a list of what you can do --
      // and Radix logs a warning when neither a description nor an explicit
      // opt-out is present.
      aria-describedby={undefined}
    >
      {/* Visually hidden rather than rendered: the palette's visible heading
          is its input placeholder, and a title bar above it would cost a row
          of vertical space on every open for text the sighted user does not
          need. Still a real DialogTitle so Radix's accessible name is set. */}
      <DialogTitle className="sr-only">{label}</DialogTitle>
      <Command
        // cmdk's own key handling covers Up/Down/Enter/Home/End. This adds
        // nothing and only stops the event reaching the Radix Dialog, which
        // would otherwise treat a bare Escape inside the list the same as one
        // on the overlay -- that is the behaviour we want, so it is left
        // alone deliberately.
        className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-caption [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-muted-foreground"
      >
        {children}
      </Command>
    </DialogContent>
  </Dialog>
);

const CommandInput = forwardRef<
  ElementRef<typeof CommandPrimitive.Input>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Input>
>(({ className, ...props }, ref) => (
  <div className="flex items-center border-b px-3">
    <Search
      className="me-2 size-4 shrink-0 text-muted-foreground"
      aria-hidden="true"
    />
    <CommandPrimitive.Input
      ref={ref}
      className={cn(
        "flex h-12 w-full bg-transparent py-3 text-body-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  </div>
));
CommandInput.displayName = CommandPrimitive.Input.displayName;

const CommandList = forwardRef<
  ElementRef<typeof CommandPrimitive.List>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.List>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.List
    ref={ref}
    className={cn("max-h-80 overflow-y-auto overflow-x-hidden p-1", className)}
    {...props}
  />
));
CommandList.displayName = CommandPrimitive.List.displayName;

const CommandEmpty = forwardRef<
  ElementRef<typeof CommandPrimitive.Empty>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>
>((props, ref) => (
  <CommandPrimitive.Empty
    ref={ref}
    className="py-6 text-center text-body-sm text-muted-foreground"
    {...props}
  />
));
CommandEmpty.displayName = CommandPrimitive.Empty.displayName;

const CommandGroup = forwardRef<
  ElementRef<typeof CommandPrimitive.Group>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Group>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.Group
    ref={ref}
    className={cn("overflow-hidden text-foreground", className)}
    {...props}
  />
));
CommandGroup.displayName = CommandPrimitive.Group.displayName;

const CommandSeparator = forwardRef<
  ElementRef<typeof CommandPrimitive.Separator>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Separator>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.Separator
    ref={ref}
    className={cn("-mx-1 h-px bg-border", className)}
    {...props}
  />
));
CommandSeparator.displayName = CommandPrimitive.Separator.displayName;

const CommandItem = forwardRef<
  ElementRef<typeof CommandPrimitive.Item>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Item>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.Item
    ref={ref}
    // min-h-11 (44px) rather than the 36px a dense list would use: WCAG 2.2
    // 2.5.8 sets the floor at 24, and this list is the app's primary
    // navigation surface on a phone, where the palette opens full-height.
    className={cn(
      "relative flex min-h-11 cursor-pointer select-none items-center gap-2 rounded-md px-3 py-2 text-body-sm outline-none data-[disabled=true]:pointer-events-none data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground data-[disabled=true]:opacity-50",
      className,
    )}
    {...props}
  />
));
CommandItem.displayName = CommandPrimitive.Item.displayName;

/** The trailing `⌘K`-style hint on a row. `ms-auto` so it pins to the end of
 *  the row in both directions. */
const CommandShortcut = ({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>) => (
  <span
    className={cn(
      "ms-auto text-caption tracking-widest text-muted-foreground",
      className,
    )}
    {...props}
  />
);
CommandShortcut.displayName = "CommandShortcut";

export {
  Command,
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandShortcut,
  CommandSeparator,
};
