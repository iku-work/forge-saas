import type { Meta, StoryObj } from "@storybook/react-vite";

import { Input } from "./input";
import { Label } from "./label";

const meta = {
  title: "Components/Label",
  component: Label,
  args: { children: "Email" },
} satisfies Meta<typeof Label>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = { args: { htmlFor: "email" } };

export const WithInput: Story = {
  render: (args) => (
    <div className="grid gap-1.5">
      <Label {...args} htmlFor="label-with-input" />
      <Input id="label-with-input" />
    </div>
  ),
};
