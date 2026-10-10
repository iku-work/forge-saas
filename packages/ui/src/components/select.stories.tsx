import type { Meta, StoryObj } from "@storybook/react-vite";

import { Select } from "./select";

const meta = {
  title: "Components/Select",
  component: Select,
  args: { "aria-label": "Status", className: "max-w-xs" },
  render: (args) => (
    <Select {...args}>
      <option value="">All statuses</option>
      <option value="active">Active</option>
      <option value="trialing">Trial</option>
      <option value="past_due">Past due</option>
    </Select>
  ),
} satisfies Meta<typeof Select>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Disabled: Story = { args: { disabled: true } };
