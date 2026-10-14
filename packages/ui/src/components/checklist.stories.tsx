import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./button";
import { Checklist, ChecklistStep } from "./checklist";

const steps = [
  { title: "Complete your profile", hint: "Add your name and a photo." },
  { title: "Upload your first file", hint: "Files are shared with everyone." },
  { title: "Invite a member", hint: "Work on it together." },
  { title: "Choose a plan", hint: "Pick what fits your organization." },
];

const meta = {
  title: "Components/Checklist",
  component: Checklist,
  args: { title: "Get started", done: 1, total: 4, progressLabel: "" },
  render: ({ done, ...args }) => (
    <Checklist
      {...args}
      done={done}
      progressLabel={`${String(done)} of ${String(args.total)} done`}
    >
      {steps.map((step, i) => (
        <ChecklistStep
          key={step.title}
          title={step.title}
          hint={step.hint}
          done={i < done}
          doneLabel="Done"
          action={
            <Button variant={i === done ? "default" : "outline"}>Open</Button>
          }
        />
      ))}
    </Checklist>
  ),
} satisfies Meta<typeof Checklist>;

export default meta;
type Story = StoryObj<typeof meta>;

export const InProgress: Story = {};
export const Fresh: Story = { args: { done: 0 } };
export const AlmostDone: Story = { args: { done: 3 } };
