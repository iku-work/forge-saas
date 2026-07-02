import { describe, expect, it } from "vitest";

import { getCurrentOrgId, orgIdRequestInterceptor, setCurrentOrgId } from "@forge/api-client";

describe("orgIdRequestInterceptor", () => {
  it("leaves the request untouched when no org is selected", () => {
    setCurrentOrgId(null);
    const request = new Request("http://localhost:8000/api/v1/auth/me");

    const result = orgIdRequestInterceptor(request);

    expect(result).toBe(request);
    expect(result.headers.has("X-Org-ID")).toBe(false);
  });

  it("sets X-Org-ID on a GET request once an org is selected", () => {
    setCurrentOrgId("org-123");
    const request = new Request("http://localhost:8000/api/v1/orgs/org-123/members");

    const result = orgIdRequestInterceptor(request);

    expect(result.headers.get("X-Org-ID")).toBe("org-123");
    setCurrentOrgId(null);
  });

  it("preserves method, headers, and body on a mutating request", async () => {
    setCurrentOrgId("org-456");
    const request = new Request("http://localhost:8000/api/v1/billing/checkout-session", {
      method: "POST",
      headers: { Authorization: "Bearer token", "Content-Type": "application/json" },
      body: JSON.stringify({ plan_slug: "pro", interval: "monthly" }),
    });

    const result = orgIdRequestInterceptor(request);

    expect(result.method).toBe("POST");
    expect(result.headers.get("Authorization")).toBe("Bearer token");
    expect(result.headers.get("X-Org-ID")).toBe("org-456");
    expect(await result.json()).toEqual({ plan_slug: "pro", interval: "monthly" });
    setCurrentOrgId(null);
  });

  it("reflects the currently selected org via getCurrentOrgId", () => {
    setCurrentOrgId("org-789");
    expect(getCurrentOrgId()).toBe("org-789");
    setCurrentOrgId(null);
    expect(getCurrentOrgId()).toBeNull();
  });
});
