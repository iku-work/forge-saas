import type { Meta, StoryObj } from "@storybook/react-vite";

import { Badge } from "./badge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "./table";

const members = [
  { name: "Alex Rivera", email: "alex@example.com", role: "Owner", status: "active" },
  { name: "Jordan Lee", email: "jordan@example.com", role: "Admin", status: "active" },
  { name: "Sam Patel", email: "sam@example.com", role: "Member", status: "invited" },
];

function MembersTable() {
  return (
    <Table>
      <TableCaption>Organization members</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Email</TableHead>
          <TableHead>Role</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {members.map((member) => (
          <TableRow key={member.email}>
            <TableCell className="font-medium">{member.name}</TableCell>
            <TableCell>{member.email}</TableCell>
            <TableCell>{member.role}</TableCell>
            <TableCell>
              <Badge variant={member.status === "active" ? "default" : "secondary"}>
                {member.status}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

const meta = {
  title: "Components/Table",
  component: MembersTable,
} satisfies Meta<typeof MembersTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
