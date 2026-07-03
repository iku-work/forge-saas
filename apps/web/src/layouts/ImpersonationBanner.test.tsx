import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImpersonationBanner } from "./ImpersonationBanner";

describe("ImpersonationBanner (T-142)", () => {
  it("renders the impersonated user's email and an End impersonation action", () => {
    render(<ImpersonationBanner email="jane@example.com" onEnd={vi.fn()} />);

    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("jane@example.com");
    expect(
      screen.getByRole("button", { name: "End impersonation" }),
    ).toBeInTheDocument();
  });

  it("calls onEnd when the End impersonation action is clicked", async () => {
    const user = userEvent.setup();
    const onEnd = vi.fn();
    render(<ImpersonationBanner email="jane@example.com" onEnd={onEnd} />);

    await user.click(screen.getByRole("button", { name: "End impersonation" }));

    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it("has no dismiss/close control -- ending impersonation is the only way to clear it", () => {
    render(<ImpersonationBanner email="jane@example.com" onEnd={vi.fn()} />);

    expect(
      screen.queryByRole("button", { name: /close|dismiss/i }),
    ).not.toBeInTheDocument();
  });
});
