import type { Permission } from "@forge/types";

import { useOrg } from "./useOrg";

// Checks what the caller can do in the currently selected org: their role's
// permissions plus anything granted to them, as the API reported them. This
// only gates UI (hiding buttons a member can't use) -- the API enforces the
// same rule server-side regardless of what's rendered here.
export function usePermission(permission: Permission): boolean {
  return useOrg().permissions.has(permission);
}
