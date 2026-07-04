import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./button";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";

function PopoverDemo() {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Open popover</Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-4 text-sm">
        Set the dimensions for the layer that appears above the content.
      </PopoverContent>
    </Popover>
  );
}

const meta = {
  title: "Components/Popover",
  component: PopoverDemo,
} satisfies Meta<typeof PopoverDemo>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Open: Story = {
  render: () => (
    <Popover open>
      <PopoverTrigger asChild>
        <Button variant="outline">Open popover</Button>
      </PopoverTrigger>
      {/* Radix's PopoverContent renders role="dialog" -- every real call
          site (e.g. NotificationBell) labels it explicitly, same as here. */}
      <PopoverContent aria-label="Dimensions" className="w-72 p-4 text-sm">
        Set the dimensions for the layer that appears above the content.
      </PopoverContent>
    </Popover>
  ),
};
