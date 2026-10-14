import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeUser } from "../test/appShellFixtures";
import { renderApp } from "../test/render";

const superadmin = makeUser(50, { is_superadmin: true });

// Ticket 12: the admin console shares the app's shell, so the Admin marker is
// the one thing that says which surface you are on. It has to be on every
// admin page, carry a word (not only a colour), and sit beside a way back.
describe("AdminLayout", () => {
  it.each(["/admin", "/admin/users"])(
    "shows the Admin marker and Back to app on %s",
    async (path) => {
      renderApp({ initialPath: path, user: superadmin });

      const marker = await screen.findByTestId("admin-marker");
      expect(marker).toHaveTextContent("Admin");
      expect(screen.getByTestId("admin-back-to-app")).toHaveAttribute(
        "href",
        "/en",
      );
    },
  );

  it("collapses to a rail with [ like the app", async () => {
    window.localStorage.clear();
    renderApp({ initialPath: "/admin", user: superadmin });
    const toggle = await screen.findByTestId("admin-sidebar-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    await userEvent.keyboard("[[");

    await waitFor(() => {
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    });
  });

  it("opens the palette over the admin pages with Ctrl+K", async () => {
    renderApp({ initialPath: "/admin", user: superadmin });
    await screen.findByTestId("admin-marker");

    await userEvent.keyboard("{Control>}k{/Control}");

    expect(
      await screen.findByTestId("palette-nav-/admin/users"),
    ).toBeInTheDocument();
    // The app's pages are not offered here; the way back to it is.
    expect(screen.queryByTestId("palette-nav-/files")).not.toBeInTheDocument();
    expect(screen.getByTestId("palette-nav-/")).toBeInTheDocument();
  });
});
