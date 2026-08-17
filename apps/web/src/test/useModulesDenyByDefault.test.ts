import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useModules } from "@forge/hooks";

// Own file (not a second `it()` in useModules.test.ts) so this gets a fresh
// module instance -- useModules' bootstrap is a module-scoped singleton
// promise, same caching behaviour as useAuth/useOrg, so a renderHook() call
// in an already-resolved file would skip straight to the resolved state
// instead of exercising the pre-resolution default. See
// appShellFixtures.ts's top comment for the same pattern applied to
// useAuth/useOrg's AppLayout integration tests.
describe("useModules (deny-by-default)", () => {
  it("denies every optional module before the config request resolves", () => {
    const { result } = renderHook(() => useModules());

    expect(result.current).toEqual({
      billing: false,
      notifications: false,
      fileStorage: false,
      featureFlags: false,
      gdpr: false,
      admin: false,
      oauth: false,
      totp2fa: false,
      multiOrg: false,
      usageMetering: false,
      outboundWebhooks: false,
      // `marketing` defaults to *on* in product config, and still reads false
      // here: deny-by-default is about what renders before /config answers,
      // not about the product's own default.
      marketing: false,
      publicApi: false,
      enterpriseSso: false,
    });
  });
});
