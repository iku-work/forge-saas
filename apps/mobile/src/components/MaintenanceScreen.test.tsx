/**
 * T-263: the maintenance screen on mobile, and the fact that the root layout
 * renders it *instead of* the routed screens rather than on top of them.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MaintenanceScreen } from "./MaintenanceScreen";

describe("MaintenanceScreen", () => {
  it("shows the operator's own copy verbatim", () => {
    render(
      <MaintenanceScreen
        maintenance={{ enabled: true, message: "Back at 03:00 UTC." }}
      />,
    );

    expect(screen.getByText("Back at 03:00 UTC.")).toBeTruthy();
  });

  it("falls back to translated copy when the operator wrote none", () => {
    render(<MaintenanceScreen maintenance={{ enabled: true, message: "" }} />);

    // The catalog's default, not an empty screen: an operator who flips the
    // switch in a hurry should not leave users staring at a blank page.
    expect(screen.getByText(/temporarily down for maintenance/i)).toBeTruthy();
  });

  it("offers no retry control", () => {
    // useMaintenance() polls /config, so the screen removes itself within a
    // minute of the window ending. A button that reloads into the same screen
    // only teaches users to hammer it.
    render(
      <MaintenanceScreen maintenance={{ enabled: true, message: "Soon." }} />,
    );

    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("root layout", () => {
  it("renders the routed screens through the maintenance gate", async () => {
    const source = await import("node:fs").then((fs) =>
      fs.readFileSync("app/_layout.tsx", "utf-8"),
    );

    // `<Slot />` must not appear directly in the layout's own tree -- if it
    // did, every screen would render behind the maintenance screen and keep
    // firing requests that all answer 503.
    expect(source).toMatch(/<RoutedScreens \/>/);
    expect(source).toMatch(
      /function RoutedScreens\(\)[\s\S]*?maintenance\.enabled[\s\S]*?<MaintenanceScreen/,
    );
  });
});
