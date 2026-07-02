import { ROLE_PERMISSIONS } from "@forge/types";
import type { Permission } from "@forge/types";

import { useOrg } from "./useOrg";

// Checks the caller's role in the currently selected org against the
// frontend's mirror of the backend's ROLE_PERMISSIONS map. This only gates
// UI (hiding buttons a member can't use) -- the API enforces the same rule
// server-side regardless of what's rendered here.
export function usePermission(permission: Permission): boolean {
  const { role } = useOrg();
  if (!role) {
    return false;
  }
  return ROLE_PERMISSIONS[role].has(permission);
}
