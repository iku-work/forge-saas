import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useModules } from "@forge/hooks";

import { fixtureConfig } from "./appShellFixtures";

describe("useModules", () => {
  it("resolves to the active modules reported by GET /api/v1/config", async () => {
    const { result } = renderHook(() => useModules());

    await waitFor(() => {
      expect(result.current.billing).toBe(true);
    });

    expect(result.current).toEqual({
      billing: fixtureConfig.modules.billing,
      notifications: fixtureConfig.modules.notifications,
      fileStorage: fixtureConfig.modules.file_storage,
      featureFlags: fixtureConfig.modules.feature_flags,
      gdpr: fixtureConfig.modules.gdpr,
      admin: fixtureConfig.modules.admin,
      oauth: fixtureConfig.modules.oauth,
      totp2fa: fixtureConfig.modules.totp_2fa,
      multiOrg: fixtureConfig.modules.multi_org,
      usageMetering: fixtureConfig.modules.usage_metering,
      outboundWebhooks: fixtureConfig.modules.outbound_webhooks,
      marketing: fixtureConfig.modules.marketing,
      publicApi: fixtureConfig.modules.public_api,
      enterpriseSso: fixtureConfig.modules.enterprise_sso,
    });
  });
});
