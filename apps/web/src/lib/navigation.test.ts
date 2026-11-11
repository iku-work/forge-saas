import { describe, expect, it } from "vitest";

import { settingsAreaFor } from "./navigation";

describe("settingsAreaFor", () => {
  it.each([
    ["/", null],
    ["/files", null],
    ["/settings/profile", "account"],
    ["/settings/notifications", "account"],
    ["/settings/org", "organization"],
    ["/settings/members", "organization"],
    // Gated pages still belong to their area.
    ["/settings/webhooks", "organization"],
    // A path no item claims falls back to the account.
    ["/settings/something-new", "account"],
    // Not a prefix match on the bare string: /settings/organizations is not
    // /settings/org.
    ["/settings/organizations", "account"],
  ])("%s -> %s", (path, area) => {
    expect(settingsAreaFor(path)).toBe(area);
  });
});
