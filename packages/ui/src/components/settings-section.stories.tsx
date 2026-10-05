import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./button";
import { Input } from "./input";
import { SettingsRow, SettingsSection } from "./settings-section";

const meta = {
  title: "Components/SettingsSection",
  component: SettingsSection,
  args: { title: "Personal details" },
} satisfies Meta<typeof SettingsSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithRowsAndSave: Story = {
  render: (args) => (
    <div className="max-w-[63rem] p-8">
      <SettingsSection {...args} footer={<Button disabled>Save</Button>}>
        <SettingsRow
          label="Name"
          hint="Shown to other members."
          htmlFor="story-name"
        >
          <Input
            id="story-name"
            className="max-w-sm"
            defaultValue="Test User"
          />
        </SettingsRow>
        <SettingsRow
          label="Email"
          hint="Used to sign in and for notifications."
        >
          <span>test@acme.io</span>
        </SettingsRow>
      </SettingsSection>
      <SettingsSection title="Preferences">
        <SettingsRow
          label="Language"
          hint="For the app and the emails we send you."
        >
          <span>English</span>
        </SettingsRow>
      </SettingsSection>
    </div>
  ),
};
