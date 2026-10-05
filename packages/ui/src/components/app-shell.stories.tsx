import {
  ArrowLeft,
  Bell,
  FolderOpen,
  LayoutDashboard,
  Settings,
  Shield,
  User,
} from "lucide-react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  AppShell,
  ShellNavGroup,
  ShellNavItem,
  ShellPanel,
  ShellSearchButton,
  ShellSidebar,
} from "./app-shell";

const meta = {
  title: "Components/AppShell",
  component: AppShell,
  parameters: { layout: "fullscreen" },
  args: { sidebar: null },
} satisfies Meta<typeof AppShell>;

export default meta;
type Story = StoryObj<typeof meta>;

function Header() {
  return (
    <>
      <ShellNavItem className="min-h-10 font-semibold text-foreground">
        <span className="grid size-[1.375rem] place-items-center rounded-sm bg-primary text-xs font-bold text-primary-foreground">
          A
        </span>
        Acme
      </ShellNavItem>
      <ShellSearchButton label="Search" shortcut="Ctrl K" />
    </>
  );
}

function Footer() {
  return (
    <div className="flex items-center gap-1">
      <ShellNavItem className="flex-1">
        <span className="grid size-7 place-items-center rounded-full bg-surface-sunken text-xs">
          TU
        </span>
        Test User
      </ShellNavItem>
      <ShellNavItem className="w-auto" aria-label="Notifications">
        <Bell />
      </ShellNavItem>
    </div>
  );
}

function Panel({ crumb, title }: { crumb: string; title: string }) {
  return (
    <ShellPanel top={crumb}>
      <div className="px-12 pt-5">
        <h1 className="type-title">{title}</h1>
      </div>
    </ShellPanel>
  );
}

export const App: Story = {
  render: () => (
    <AppShell
      className="min-h-dvh bg-background"
      sidebar={
        <ShellSidebar header={<Header />} footer={<Footer />}>
          <ShellNavGroup>
            <ShellNavItem aria-current="page">
              <LayoutDashboard />
              Dashboard
            </ShellNavItem>
            <ShellNavItem>
              <FolderOpen />
              Files
            </ShellNavItem>
            <ShellNavItem>
              <Settings />
              Settings
            </ShellNavItem>
          </ShellNavGroup>
        </ShellSidebar>
      }
    >
      <Panel crumb="Dashboard" title="Welcome to Acme" />
    </AppShell>
  ),
};

export const Settings_: Story = {
  name: "Settings mode",
  render: () => (
    <AppShell
      className="min-h-dvh bg-background"
      sidebar={
        <ShellSidebar header={<Header />} footer={<Footer />}>
          <ShellNavItem>
            <ArrowLeft />
            Back to app
          </ShellNavItem>
          <ShellNavGroup label="Your account">
            <ShellNavItem aria-current="page">
              <User />
              Profile
            </ShellNavItem>
            <ShellNavItem>
              <Shield />
              Security
            </ShellNavItem>
          </ShellNavGroup>
          <ShellNavGroup label="Acme">
            <ShellNavItem>
              <Settings />
              General
            </ShellNavItem>
          </ShellNavGroup>
        </ShellSidebar>
      }
    >
      <Panel crumb="Settings / Profile" title="Profile" />
    </AppShell>
  ),
};
