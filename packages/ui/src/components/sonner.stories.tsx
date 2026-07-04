import { useEffect } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./button";
import { Toaster, toast } from "./sonner";

function ToasterDemo() {
  return (
    <div>
      <Toaster />
      <Button
        variant="outline"
        onClick={() =>
          toast("Invitation sent", { description: "jordan@example.com will get an email shortly." })
        }
      >
        Show toast
      </Button>
    </div>
  );
}

function ToasterVisible() {
  useEffect(() => {
    toast("Invitation sent", {
      description: "jordan@example.com will get an email shortly.",
      duration: Infinity,
    });
  }, []);
  return <Toaster />;
}

const meta = {
  title: "Components/Toaster",
  component: ToasterDemo,
} satisfies Meta<typeof ToasterDemo>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Visible: Story = {
  render: () => <ToasterVisible />,
};
