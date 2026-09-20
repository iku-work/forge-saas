import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationPublic } from "@forge/api-client";

import { makeNotification } from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import {
  getVirtualScrollContainer,
  installVirtualLayout,
  scrollToBottom,
} from "../../test/virtualLayout";
import { NotificationFeed } from "./NotificationFeed";

// T-241: the notification centre's list, driven against the real
// @tanstack/react-virtual. NotificationBell.test.tsx mocks the virtualizer
// away because it asserts on *which* notifications are on screen; this file
// is the opposite -- it asserts on how few of them are in the DOM, and on
// the keyboard/scroll behaviour that only exists because of virtualization.

// Must match NotificationFeed's own constants.
const ROW_HEIGHT_PX = 76;
const VIEWPORT_HEIGHT_PX = 384;

const MAX_ROW_NODES = 50;

// Comfortably past the initially rendered range (viewport / row height plus
// overscan on both sides), so focus has to cross a virtualization boundary
// to get there.
const ROWS_TO_WALK = 20;

function makeNotifications(count: number): NotificationPublic[] {
  return Array.from({ length: count }, (_, index) =>
    makeNotification(index, { title: `Notification ${String(index)}` }),
  );
}

function renderFeed(
  notifications: NotificationPublic[],
  overrides: Partial<Parameters<typeof NotificationFeed>[0]> = {},
) {
  return render(
    <NotificationFeed
      notifications={notifications}
      isLoading={false}
      isError={false}
      hasMore={false}
      isLoadingMore={false}
      onLoadMore={vi.fn()}
      onMarkRead={vi.fn()}
      {...overrides}
    />,
  );
}

describe("NotificationFeed virtualization", () => {
  let restoreLayout: () => void;

  beforeEach(() => {
    restoreLayout = installVirtualLayout({
      rowHeight: ROW_HEIGHT_PX,
      viewportHeight: VIEWPORT_HEIGHT_PX,
    });
  });

  afterEach(() => {
    restoreLayout();
  });

  it("renders far fewer row nodes than notifications for a 1,000-item feed", () => {
    renderFeed(makeNotifications(1000));

    const rows = screen.getAllByRole("listitem");
    expect(rows.length).toBeLessThan(MAX_ROW_NODES);
    expect(rows.length).toBeGreaterThan(0);
    expect(screen.getByRole("list")).toHaveStyle({
      height: `${String(1000 * ROW_HEIGHT_PX)}px`,
    });
  });

  it("scrolling to the bottom of the loaded rows asks for the next page exactly once", () => {
    const onLoadMore = vi.fn();
    renderFeed(makeNotifications(60), { hasMore: true, onLoadMore });

    expect(onLoadMore).not.toHaveBeenCalled();

    const container = getVirtualScrollContainer();
    act(() => {
      scrollToBottom(container);
    });

    expect(onLoadMore).toHaveBeenCalledTimes(1);

    // Sitting at the bottom must not re-request the same page: a scroll
    // event that doesn't change the rendered range changes nothing.
    act(() => {
      container.dispatchEvent(new Event("scroll"));
    });
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  // Given its own, longer budget: this is the heaviest test in the web suite
  // -- 20 sequential user-event keyboard round trips against a 200-row
  // virtualized list, each of which re-renders the range and re-runs the
  // measurement stubs. It was already failing intermittently under full
  // file-level parallelism before this budget existed (and before the suite
  // grew), always by running out of time rather than by asserting anything
  // different. The file-wide 15s in vitest.config.ts is the right default;
  // this one test genuinely needs more headroom on a loaded machine.
  it("arrow keys move focus through rows across a virtualization boundary", async () => {
    renderFeed(makeNotifications(200));

    const firstRow = screen.getAllByRole("listitem")[0];
    expect(firstRow).toBeDefined();
    act(() => {
      firstRow?.focus();
    });

    for (let step = 0; step < ROWS_TO_WALK; step++) {
      await userEvent.keyboard("{ArrowDown}");
    }

    expect(document.activeElement).toHaveAttribute(
      "data-index",
      String(ROWS_TO_WALK),
    );
    // The boundary was genuinely crossed -- the row focus started on has
    // been unmounted, so a naive "focus the next sibling" implementation
    // would have dropped focus to <body> along the way.
    expect(
      document.querySelector('[role="listitem"][data-index="0"]'),
    ).toBeNull();
    expect(document.activeElement).toHaveAttribute("role", "listitem");

    await userEvent.keyboard("{ArrowUp}");
    expect(document.activeElement).toHaveAttribute(
      "data-index",
      String(ROWS_TO_WALK - 1),
    );
  }, 45_000);

  it("has no a11y violations with the list virtualized", async () => {
    const { container } = renderFeed(makeNotifications(200));

    await expectNoA11yViolations(container);
  });
});
