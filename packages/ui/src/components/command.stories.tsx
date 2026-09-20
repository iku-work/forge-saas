import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  CreditCard,
  FolderOpen,
  LayoutDashboard,
  Moon,
  Settings,
} from "lucide-react";

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "./command";

function Rows() {
  return (
    <CommandList>
      <CommandEmpty>No matches. Try a different word.</CommandEmpty>
      <CommandGroup heading="Go to">
        <CommandItem value="Dashboard /">
          <LayoutDashboard className="size-4 shrink-0" aria-hidden="true" />
          Dashboard
        </CommandItem>
        <CommandItem value="Files /files">
          <FolderOpen className="size-4 shrink-0" aria-hidden="true" />
          Files
        </CommandItem>
        <CommandItem value="Billing /settings/billing">
          <CreditCard className="size-4 shrink-0" aria-hidden="true" />
          Billing
        </CommandItem>
      </CommandGroup>
      <CommandSeparator />
      <CommandGroup heading="Actions">
        <CommandItem value="Toggle theme">
          <Moon className="size-4 shrink-0" aria-hidden="true" />
          Toggle theme
        </CommandItem>
        <CommandItem value="Keyboard shortcuts">
          <Settings className="size-4 shrink-0" aria-hidden="true" />
          Keyboard shortcuts
          <CommandShortcut>?</CommandShortcut>
        </CommandItem>
      </CommandGroup>
    </CommandList>
  );
}

const meta = {
  title: "Components/Command",
  component: Command,
} satisfies Meta<typeof Command>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Inline, so the list can be inspected without a modal in the way. */
export const Inline: Story = {
  render: () => (
    <Command className="w-96 rounded-lg border shadow-card">
      <CommandInput placeholder="Search pages and actions..." />
      <Rows />
    </Command>
  ),
};

/** How it actually ships: over the page, opened with the hotkey. */
export const AsDialog: Story = {
  render: () => (
    <CommandDialog open label="Command palette">
      <CommandInput placeholder="Search pages and actions..." />
      <Rows />
    </CommandDialog>
  ),
};

/** The state that matters most for copy: a search with no results still has
 *  to tell the user what to do next (F-010/F-011). */
export const NoResults: Story = {
  render: () => (
    <Command className="w-96 rounded-lg border shadow-card" value="">
      <CommandInput placeholder="Search pages and actions..." value="zzzzzz" />
      <Rows />
    </Command>
  ),
};
