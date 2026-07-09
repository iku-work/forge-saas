import type { Meta, StoryObj } from "@storybook/react-vite";

import { Label } from "./label";
import { PasswordInput } from "./password-input";

const meta = {
  title: "Components/PasswordInput",
  component: PasswordInput,
  args: {
    placeholder: "••••••••••••",
  },
} satisfies Meta<typeof PasswordInput>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const WithLabel: Story = {
  render: (args) => (
    <div className="grid w-full max-w-sm gap-1.5">
      <Label htmlFor="password">Password</Label>
      <PasswordInput id="password" {...args} />
    </div>
  ),
};
