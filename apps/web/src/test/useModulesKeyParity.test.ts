import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useModules } from "@forge/hooks";
import type { ProductConfig } from "@forge/types";

// The compile-time leg. TypeScript forces this literal to name every key of
// ProductConfig["modules"] exactly once -- a missing key and an excess one are
// both errors -- so it is a *checked* transcription of the type rather than a
// copy that can drift.
//
// The two runtime objects it is compared against below are the ones the type
// cannot reach: `DENY_BY_DEFAULT` (what every caller reads before /config
// answers) and `toActiveModules()`'s return (what they read afterwards).
// Forgetting a key in the first is the dangerous half -- it reads `undefined`,
// which is falsy today and becomes a render-it-early bug the moment someone
// writes `!modules.publicApi`.
//
// The third mirror of this list, the shipped root product.config.ts, is
// checked from the backend instead (backend/tests/test_product_config_parity.py):
// no tsconfig in the workspace includes a file outside its own `src`.
const MODULE_KEYS: Record<keyof ProductConfig["modules"], true> = {
  billing: true,
  notifications: true,
  fileStorage: true,
  featureFlags: true,
  gdpr: true,
  admin: true,
  oauth: true,
  totp2fa: true,
  multiOrg: true,
  usageMetering: true,
  outboundWebhooks: true,
  marketing: true,
  publicApi: true,
  enterpriseSso: true,
};

describe("useModules (key parity)", () => {
  it("exposes exactly the ProductConfig module keys, before and after /config resolves", async () => {
    const declared = Object.keys(MODULE_KEYS).sort();

    // First render happens before the bootstrap fetch can resolve, so this
    // reads DENY_BY_DEFAULT -- the same one-shot module-scoped promise
    // useModulesDenyByDefault.test.ts needs its own file for.
    const { result } = renderHook(() => useModules());
    expect(Object.keys(result.current).sort()).toEqual(declared);

    // ...and this reads toActiveModules(), applied to the default handler's
    // ConfigResponse (fixtureConfig, where billing is on).
    await waitFor(() => {
      expect(result.current.billing).toBe(true);
    });
    expect(Object.keys(result.current).sort()).toEqual(declared);
  });
});
