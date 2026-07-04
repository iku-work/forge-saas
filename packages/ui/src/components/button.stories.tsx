import { Loader2, Mail } from "lucide-react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./button";

const meta = {
  title: "Components/Button",
  component: Button,
  args: {
    children: "Button",
  },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Destructive: Story = { args: { variant: "destructive" } };
export const Outline: Story = { args: { variant: "outline" } };
export const Secondary: Story = { args: { variant: "secondary" } };
export const Ghost: Story = { args: { variant: "ghost" } };
export const Link: Story = { args: { variant: "link" } };

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Button size="sm">Small</Button>
      <Button size="default">Default</Button>
      <Button size="lg">Large</Button>
      <Button size="icon" aria-label="Send">
        <Mail />
      </Button>
    </div>
  ),
};

export const Loading: Story = {
  // Button has no built-in loading prop -- every call site composes
  // disabled + a spinner icon itself (see FileUploader's own StatusIcon).
  render: () => (
    <Button disabled>
      <Loader2 className="animate-spin" aria-hidden="true" />
      Saving...
    </Button>
  ),
};

export const Disabled: Story = { args: { disabled: true } };
