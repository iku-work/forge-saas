import { screen } from "@testing-library/react";
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
});
