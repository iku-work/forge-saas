import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  DataListColumns,
  DataListMuted,
  DataListRow,
  RowActionsButton,
} from "./data-list";
import { StatusPill } from "./status-pill";

const meta = {
  title: "Components/DataList",
  component: DataListRow,
} satisfies Meta<typeof DataListRow>;

export default meta;
type Story = StoryObj<typeof meta>;

const COLUMNS = "grid-cols-[minmax(0,1fr)_6.5rem_6.5rem_7rem_2rem]";

const rows = [
  {
    name: "Maya Lindqvist",
    role: "Admin",
    joined: "14 Sep 2026",
    pending: false,
  },
  {
    name: "Jonas Weber",
    role: "Member",
    joined: "18 Sep 2026",
    pending: false,
  },
  {
    name: "priya@acme.io",
    role: "Member",
    joined: "Sent 1 Oct",
    pending: true,
  },
];

export const Members: Story = {
  render: () => (
    <div className="max-w-[63rem] p-8">
      <DataListColumns className={COLUMNS}>
        <span>Member</span>
        <span>Role</span>
        <span>Status</span>
        <span>Joined</span>
        <span />
      </DataListColumns>
      <div role="list" aria-label="Members">
        {rows.map((row) => (
          <DataListRow key={row.name} role="listitem" className={COLUMNS}>
            <span className="truncate font-medium">{row.name}</span>
            <span>{row.role}</span>
            <span>
              <StatusPill tone={row.pending ? "pending" : "active"}>
                {row.pending ? "Invited" : "Active"}
              </StatusPill>
            </span>
            <DataListMuted>{row.joined}</DataListMuted>
            <span className="text-end">
              <RowActionsButton aria-label={`Actions for ${row.name}`} />
            </span>
          </DataListRow>
        ))}
      </div>
    </div>
  ),
};
