import type { Meta, StoryObj } from "@storybook/react-vite";

import { StatusPill } from "./status-pill";

const meta = {
  title: "Components/StatusPill",
  component: StatusPill,
  args: { children: "Active", tone: "active" },
} satisfies Meta<typeof StatusPill>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Active: Story = {};
export const Pending: Story = {
  args: { tone: "pending", children: "Invited" },
};
export const Neutral: Story = {
  args: { tone: "neutral", children: "Revoked" },
};
export const Danger: Story = { args: { tone: "danger", children: "Failed" } };
