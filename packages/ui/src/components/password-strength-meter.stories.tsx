import type { Meta, StoryObj } from "@storybook/react-vite";

import { PasswordStrengthMeter } from "./password-strength-meter";

const meta = {
  title: "Components/PasswordStrengthMeter",
  component: PasswordStrengthMeter,
} satisfies Meta<typeof PasswordStrengthMeter>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Weak: Story = { args: { password: "short" } };
export const Fair: Story = { args: { password: "lowercaseonly" } };
export const Good: Story = { args: { password: "lowercasepassword" } };
export const Strong: Story = { args: { password: "Correct-Horse-99" } };
