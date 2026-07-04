import type { Meta, StoryObj } from "@storybook/react-vite";

import { LocaleSwitcher } from "./locale-switcher";

const meta = {
  title: "Components/LocaleSwitcher",
  component: LocaleSwitcher,
  parameters: {
    // useLocale() (@forge/hooks) calls GET /api/v1/config on mount to learn
    // the product's supported locales -- left unmocked deliberately, since
    // it fails closed to just ["en"] (see useLocale.ts's deny-by-default
    // comment) and this story doesn't need more than one locale to render.
    msw: { handlers: [] },
  },
} satisfies Meta<typeof LocaleSwitcher>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
