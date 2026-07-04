import type { Meta, StoryObj } from "@storybook/react-vite";

import { Input } from "./input";
import { Label } from "./label";

const meta = {
  title: "Components/Input",
  component: Input,
  args: {
    placeholder: "you@example.com",
  },
} satisfies Meta<typeof Input>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const WithLabel: Story = {
  render: (args) => (
    <div className="grid w-full max-w-sm gap-1.5">
      <Label htmlFor="email">Email</Label>
      <Input id="email" {...args} />
    </div>
  ),
};

export const WithError: Story = {
  render: (args) => (
    <div className="grid w-full max-w-sm gap-1.5">
      <Label htmlFor="email-error" className="text-destructive">
        Email
      </Label>
      <Input
        id="email-error"
        aria-invalid
        aria-describedby="email-error-message"
        className="border-destructive"
        {...args}
      />
      <p id="email-error-message" className="text-sm font-medium text-destructive">
        Enter a valid email address.
      </p>
    </div>
  ),
};

export const Disabled: Story = { args: { disabled: true, value: "readonly@example.com" } };
