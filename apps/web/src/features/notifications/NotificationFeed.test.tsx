import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationPublic } from "@forge/api-client";

import { makeNotification } from "../../test/appShellFixtures";
import { installVirtualLayout } from "../../test/virtualLayout";
import { NotificationFeed } from "./NotificationFeed";

// One inbox spans every org the reader is in: a row names its org when there
// is more than one to tell apart, and following its link says which org first.

const ACME = "11111111-1111-1111-1111-111111111111";
const GLOBEX = "22222222-2222-2222-2222-222222222222";

function renderFeed(
  notifications: NotificationPublic[],
  orgNames?: ReadonlyMap<string, string>,
  onOpen = vi.fn(),
) {
  render(
    <NotificationFeed
      notifications={notifications}
      isLoading={false}
      isError={false}
      hasMore={false}
      isLoadingMore={false}
      onLoadMore={vi.fn()}
      onMarkRead={vi.fn()}
      orgNames={orgNames}
      onOpen={onOpen}
    />,
  );
  return onOpen;
}

describe("NotificationFeed across orgs", () => {
  let restoreLayout: () => void;

  beforeEach(() => {
    restoreLayout = installVirtualLayout({
      rowHeight: 76,
      viewportHeight: 384,
    });
  });

  afterEach(() => {
    restoreLayout();
  });

  it("labels each row with its org, and leaves personal ones unlabelled", () => {
    renderFeed(
      [
        makeNotification(1, { org_id: ACME }),
        makeNotification(2, { org_id: GLOBEX }),
        makeNotification(3, { org_id: null }),
      ],
      new Map([
        [ACME, "Acme"],
        [GLOBEX, "Globex"],
      ]),
    );

    expect(
      screen.getAllByTestId("notification-org").map((el) => el.textContent),
    ).toEqual(["Acme", "Globex"]);
  });

  it("shows no label when the reader is in one org", () => {
    renderFeed([makeNotification(1, { org_id: ACME })]);

    expect(screen.queryByTestId("notification-org")).toBeNull();
  });

  it("hands the notification over before its link is followed", async () => {
    const notification = makeNotification(1, {
      org_id: GLOBEX,
      action_url: "#billing",
    });
    const onOpen = renderFeed([notification]);

    await userEvent.click(screen.getByRole("link"));

    expect(onOpen).toHaveBeenCalledWith(notification);
  });
});
