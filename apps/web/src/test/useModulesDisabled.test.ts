import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { useModules } from "@forge/hooks";

import { envelope, fixtureConfig } from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();

describe("useModules (a module disabled in product config)", () => {
  it("reports false only for the module the backend disabled", async () => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: { ...fixtureConfig.modules, billing: false },
          }),
        ),
      ),
    );

    const { result } = renderHook(() => useModules());

    await waitFor(() => {
      expect(result.current.billing).toBe(false);
    });

    expect(result.current.gdpr).toBe(true);
  });
});
