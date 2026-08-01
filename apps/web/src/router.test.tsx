import { waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { UserPublic } from "@forge/api-client";

import { makeUser } from "./test/appShellFixtures";
import { renderApp } from "./test/render";

const unverifiedUser: UserPublic = { ...makeUser(10), email_verified_at: null };

describe("/_app auth guard", () => {
  it("redirects to /login when there is no authenticated user", async () => {
    const { router } = renderApp({ initialPath: "/", user: null });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en/login");
    });
  });

  it("redirects to /verify-email when the user has not verified their email", async () => {
    const { router } = renderApp({ initialPath: "/", user: unverifiedUser });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en/verify-email");
    });
  });

  // Authenticated rendering through the guarded `_app` layout (dashboard,
  // settings pages) is covered in layouts/AppLayout.test.tsx instead of
  // here -- that suite needs to mock useAuth's and useOrg's own bootstrap
  // calls (orgs, membership) for AppLayout to render its real shell rather
  // than the "create your organization" empty state, and doing that here
  // would leave a resolved bootstrapPromise behind for the redirect tests
  // above, which mount /login's LoginPage (itself a useAuth caller) and
  // need that bootstrap to resolve to a logged-out user.
});
