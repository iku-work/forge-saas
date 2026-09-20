import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SEQUENCE_TIMEOUT_MS } from "./registry";
import {
  useGlobalShortcuts,
  type GlobalShortcutHandlers,
} from "./useGlobalShortcuts";

function Harness(handlers: GlobalShortcutHandlers) {
  useGlobalShortcuts(handlers);
  return <input aria-label="a field" />;
}

function setup(overrides: Partial<GlobalShortcutHandlers> = {}) {
  const handlers: GlobalShortcutHandlers = {
    onCommandPalette: vi.fn(),
    onShowShortcuts: vi.fn(),
    onNavigate: vi.fn(),
    ...overrides,
  };
  render(<Harness {...handlers} />);
  return handlers;
}

describe("useGlobalShortcuts", () => {
  it("opens the palette on Ctrl+K", async () => {
    const handlers = setup();

    await userEvent.keyboard("{Control>}k{/Control}");

    expect(handlers.onCommandPalette).toHaveBeenCalledTimes(1);
  });

  it("opens the palette on Meta+K", async () => {
    const handlers = setup();

    await userEvent.keyboard("{Meta>}k{/Meta}");

    expect(handlers.onCommandPalette).toHaveBeenCalledTimes(1);
  });

  it("opens the palette even from inside a text field", async () => {
    // The one case that must pierce the typing guard: a chord cannot be
    // produced by ordinary typing, and a palette that refuses to open because
    // focus happens to sit in a field is the opposite of useful.
    const handlers = setup();
    await userEvent.click(screen.getByLabelText("a field"));

    await userEvent.keyboard("{Control>}k{/Control}");

    expect(handlers.onCommandPalette).toHaveBeenCalledTimes(1);
  });

  it("shows the cheat sheet on ?", async () => {
    const handlers = setup();

    await userEvent.keyboard("?");

    expect(handlers.onShowShortcuts).toHaveBeenCalledTimes(1);
  });

  it("does not show the cheat sheet while typing", async () => {
    const handlers = setup();
    await userEvent.click(screen.getByLabelText("a field"));

    await userEvent.keyboard("?");

    expect(handlers.onShowShortcuts).not.toHaveBeenCalled();
  });

  it("navigates on a g-then-letter sequence", async () => {
    const handlers = setup();

    await userEvent.keyboard("gf");

    expect(handlers.onNavigate).toHaveBeenCalledWith("/files");
  });

  it("does not navigate on a sequence typed into a field", async () => {
    // "g" then "d" is an ordinary thing to type into a name box. Navigating
    // away would discard the form.
    const handlers = setup();
    await userEvent.click(screen.getByLabelText("a field"));

    await userEvent.keyboard("gd");

    expect(handlers.onNavigate).not.toHaveBeenCalled();
  });

  it("ignores a stale lead key", async () => {
    // `Date.now` rather than vi.useFakeTimers(): user-event schedules its own
    // work on timers, so faking them deadlocks its `await` against a clock
    // only the test advances. The handler reads the wall clock directly, so
    // moving that is both sufficient and the narrower intervention.
    const handlers = setup();
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    try {
      await userEvent.keyboard("g");
      now += SEQUENCE_TIMEOUT_MS + 1;
      await userEvent.keyboard("f");

      expect(handlers.onNavigate).not.toHaveBeenCalled();
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("clears the lead key when an unrelated key follows", async () => {
    const handlers = setup();

    // "g", then a key that starts no sequence, then "f" -- the "f" must not
    // complete the sequence the "g" began.
    await userEvent.keyboard("gxf");

    expect(handlers.onNavigate).not.toHaveBeenCalled();
  });

  it("treats a second g as a fresh lead", async () => {
    const handlers = setup();

    await userEvent.keyboard("ggf");

    expect(handlers.onNavigate).toHaveBeenCalledWith("/files");
  });

  it("leaves other modifier chords alone", async () => {
    // Ctrl+S is the browser's (or a form's). Nothing here should claim it.
    const handlers = setup();

    await userEvent.keyboard("{Control>}s{/Control}");

    expect(handlers.onCommandPalette).not.toHaveBeenCalled();
    expect(handlers.onNavigate).not.toHaveBeenCalled();
    expect(handlers.onShowShortcuts).not.toHaveBeenCalled();
  });
});
