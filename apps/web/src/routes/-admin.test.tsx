import { waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeUser } from "../test/appShellFixtures";
import { renderApp } from "../test/render";

const superadmin = makeUser(20, { is_superadmin: true });
const plainUser = makeUser(21, { is_superadmin: false });

describe("/admin auth guard", () => {
  it("redirects to /login when there is no authenticated user", async () => {
    const { router } = renderApp({ initialPath: "/admin", user: null });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en/login");
    });
  });

  it("redirects a non-superadmin back to the user-facing app", async () => {
    const { router } = renderApp({ initialPath: "/admin", user: plainUser });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en");
    });
  });

  it("does not redirect a superadmin away from /admin", async () => {
    const { router } = renderApp({ initialPath: "/admin", user: superadmin });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en/admin");
    });
  });
});
