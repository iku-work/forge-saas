import type { Meta, StoryObj } from "@storybook/react-vite";

import { Avatar, AvatarFallback, AvatarImage } from "./avatar";

const meta = {
  title: "Components/Avatar",
  component: Avatar,
} satisfies Meta<typeof Avatar>;

export default meta;
type Story = StoryObj<typeof meta>;

// Inline SVG data URI rather than a hosted image -- stories shouldn't depend
// on the network being reachable (CI, offline dev, storybook:test's a11y run).
const PLACEHOLDER_AVATAR =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="#7c6ff7"/><text x="50%" y="54%" font-size="16" fill="white" text-anchor="middle" dominant-baseline="middle" font-family="sans-serif">AR</text></svg>',
  );

export const WithImage: Story = {
  render: () => (
    <Avatar>
      <AvatarImage src={PLACEHOLDER_AVATAR} alt="Alex Rivera" />
      <AvatarFallback>AR</AvatarFallback>
    </Avatar>
  ),
};

export const FallbackInitials: Story = {
  render: () => (
    <Avatar>
      {/* No src -- exercises the fallback-only path. */}
      <AvatarFallback>JL</AvatarFallback>
    </Avatar>
  ),
};
