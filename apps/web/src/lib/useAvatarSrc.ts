import { useAuth } from "@forge/hooks";

/**
 * The current user's avatar image URL, or null when there is nothing to show.
 *
 * Shared by the shell's user menu and the profile page, so the two cannot
 * drift: the menu used to show initials forever. `avatar_url` is already the
 * right picture -- an uploaded avatar's public URL, else an OAuth picture --
 * and it changes with every new upload, so a cached image is never stale.
 */
export function useAvatarSrc(): string | null {
  const { user } = useAuth();
  return user?.avatar_url ?? null;
}
