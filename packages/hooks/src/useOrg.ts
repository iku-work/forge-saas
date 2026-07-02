import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  listMembersApiV1OrgsOrgIdMembersGet,
  listMyOrgsApiV1OrgsGet,
  setCurrentOrgId,
} from "@forge/api-client";
import type { MemberRole, OrganizationPublic } from "@forge/api-client";

import { useAuth } from "./useAuth";

// Persists the user's org switcher choice across reloads -- without this,
// every page load would fall back to "first org in the list", which is
// disorienting for a user who deliberately switched to a different one.
const STORAGE_KEY = "forge.currentOrgId";

function readStoredOrgId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage can throw in locked-down environments (private browsing in
    // some browsers, sandboxed iframes); falling back to "no stored org" is
    // strictly better than crashing the app shell over a persistence nicety.
    return null;
  }
}

function writeStoredOrgId(orgId: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, orgId);
  } catch {
    // See readStoredOrgId.
  }
}

interface OrgState {
  orgs: OrganizationPublic[];
  currentOrgId: string | null;
  role: MemberRole | null;
  isLoading: boolean;
}

let state: OrgState = { orgs: [], currentOrgId: null, role: null, isLoading: true };
const listeners = new Set<() => void>();

function setState(next: Partial<OrgState>): void {
  state = { ...state, ...next };
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): OrgState {
  return state;
}

// Resolves the caller's own role in `orgId` by reading it off the member
// list rather than a dedicated endpoint -- GET /orgs/{id}/members is granted
// to MEMBERS_READ, which every role has, and the member who's asking for
// their own role is always present on the first page (capped at the API's
// max page size), so this needs no pagination beyond that for a phase-1
// template's org sizes.
async function loadRole(userId: string, orgId: string): Promise<MemberRole | null> {
  try {
    const { data } = await listMembersApiV1OrgsOrgIdMembersGet({
      path: { org_id: orgId },
      query: { limit: 100 },
      throwOnError: true,
    });
    const members = data.data ?? [];
    return members.find((member) => member.user_id === userId)?.role ?? null;
  } catch {
    return null;
  }
}

function pickOrgId(orgs: OrganizationPublic[]): string | null {
  const stored = readStoredOrgId();
  if (stored && orgs.some((org) => org.id === stored)) {
    return stored;
  }
  return orgs[0]?.id ?? null;
}

// Tracks which user the current store contents belong to, so a logout
// followed by a different user logging in re-bootstraps instead of
// momentarily showing the previous user's orgs.
let loadedForUserId: string | null = null;

async function bootstrap(userId: string): Promise<void> {
  loadedForUserId = userId;
  setState({ isLoading: true });
  try {
    const { data } = await listMyOrgsApiV1OrgsGet({ throwOnError: true });
    const orgs = data.data ?? [];
    const currentOrgId = pickOrgId(orgs);
    setCurrentOrgId(currentOrgId);
    const role = currentOrgId ? await loadRole(userId, currentOrgId) : null;
    setState({ orgs, currentOrgId, role, isLoading: false });
  } catch {
    setCurrentOrgId(null);
    setState({ orgs: [], currentOrgId: null, role: null, isLoading: false });
  }
}

export interface UseOrgResult {
  orgs: OrganizationPublic[];
  org: OrganizationPublic | null;
  role: MemberRole | null;
  isLoading: boolean;
  switchOrg: (orgId: string) => void;
  refreshOrgs: () => Promise<void>;
}

export function useOrg(): UseOrgResult {
  const { user } = useAuth();
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);

  useEffect(() => {
    if (!user) {
      if (loadedForUserId !== null) {
        loadedForUserId = null;
        setCurrentOrgId(null);
        setState({ orgs: [], currentOrgId: null, role: null, isLoading: false });
      }
      return;
    }
    if (loadedForUserId !== user.id) {
      void bootstrap(user.id);
    }
  }, [user]);

  const switchOrg = useCallback(
    (orgId: string): void => {
      writeStoredOrgId(orgId);
      setCurrentOrgId(orgId);
      setState({ currentOrgId: orgId, role: null });
      const userId = user?.id;
      if (userId) {
        void loadRole(userId, orgId).then((role) => {
          setState({ role });
        });
      }
    },
    [user],
  );

  const refreshOrgs = useCallback(async (): Promise<void> => {
    if (user) {
      await bootstrap(user.id);
    }
  }, [user]);

  const org = snapshot.orgs.find((candidate) => candidate.id === snapshot.currentOrgId) ?? null;

  return {
    orgs: snapshot.orgs,
    org,
    role: snapshot.role,
    isLoading: snapshot.isLoading,
    switchOrg,
    refreshOrgs,
  };
}
