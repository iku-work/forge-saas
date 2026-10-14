import { describe, expect, it } from "vitest";

import { renderChord } from "./registry";

describe("renderChord", () => {
  it("joins with + off Apple, so the hint does not read as the word CtrlK", () => {
    expect(renderChord(["mod", "K"], false)).toBe("Ctrl+K");
  });

  it("runs the glyphs together on Apple, as macOS menus do", () => {
    expect(renderChord(["mod", "K"], true)).toBe("⌘K");
  });
});
