import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./button";
import { Page, PageHeader } from "./page-header";

const meta = {
  title: "Components/PageHeader",
  component: PageHeader,
  args: {
    title: "Members",
    description:
      "People who can sign in to your organization, and what they can do.",
  },
  decorators: [
    (Story) => (
      <Page>
        <Story />
      </Page>
    ),
  ],
} satisfies Meta<typeof PageHeader>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const WithAction: Story = {
  args: { actions: <Button>Invite member</Button> },
};

export const TitleOnly: Story = {
  args: { description: undefined },
};
