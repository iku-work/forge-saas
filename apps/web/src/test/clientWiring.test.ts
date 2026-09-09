/**
 * The assembled client, not its parts.
 *
 * `authInterceptor.test.ts` calls `authRetryInterceptor` directly, which
 * proves the function and nothing about the client it is registered on.
 * Every interceptor here shares one chain, and the error interceptor
 * (`attachHttpStatus`) was added to that chain right next to the one that
 * keeps a session alive -- so "does registering it disturb the 401 refresh?"
 * is a question no existing test could answer.
 *
 * These drive the real exported `client` singleton with its real
 * registrations, and only stub `fetch`.
 */
import { client, httpStatusOf, setAccessToken } from "@forge/api-client";
import { afterEach, describe, expect, it, vi } from "vitest";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const envelope = (data: unknown) => ({ data, error: null, meta: { request_id: "r" } });

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

describe("the assembled api client", () => {
  it("still refreshes and replays a 401, with the error interceptor registered", async () => {
    setAccessToken("expired");
    let served = 0;
    let replayAuth: string | null = null;
    vi.stubGlobal(
      "fetch",
      // The two calls arrive in different shapes, which is why this reads
      // both. openapi-fetch issues a Request; authRetryInterceptor's replay
      // is a bare `fetch(request.url, { headers })`. Annotating this
      // parameter `string` and matching on `String(url)` yields
      // "[object Request]", so the refresh branch never fired and this test
      // passed without ever exercising the path it names.
      vi.fn((input: Request | URL | string, init?: RequestInit) => {
        const url =
          typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.includes("/auth/refresh")) {
          return Promise.resolve(jsonResponse(200, envelope({ access_token: "fresh" })));
        }
        if (served === 1) {
          replayAuth = new Headers(init?.headers).get("Authorization");
        }
        served += 1;
        // First call is the expired token; the replay carries the new one.
        return Promise.resolve(
          served === 1
            ? new Response(null, { status: 401 })
            : jsonResponse(200, envelope({ ok: true })),
        );
      }),
    );

    const result = await client.get({ url: "/api/v1/me" });

    expect(result.error).toBeUndefined();
    expect(result.data).toEqual(envelope({ ok: true }));
    // The point of a refresh is the *new* token on the replay. Asserting only
    // that the retry succeeded cannot tell a working refresh from a second
    // attempt with the same dead credential.
    expect(replayAuth).toBe("Bearer fresh");
  });

  it("annotates a failure with its status without disturbing the envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(402, {
            data: null,
            error: { code: "plan_limit", message: "Upgrade required" },
            meta: { request_id: "r" },
          }),
        ),
      ),
    );

    const result = await client.get({ url: "/api/v1/files" });

    expect(httpStatusOf(result.error)).toBe(402);
    // The annotation must be invisible to anything that renders or
    // serialises the error -- it is extra information, not a change of shape.
    expect(JSON.parse(JSON.stringify(result.error))).toEqual({
      data: null,
      error: { code: "plan_limit", message: "Upgrade required" },
      meta: { request_id: "r" },
    });
  });

  it("survives a network failure, where there is no response to read a status from", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );

    // The interceptor takes `response` as possibly-undefined for exactly this
    // path; touching `.status` here would throw while handling the error it
    // was meant to annotate.
    const result = await client.get({ url: "/api/v1/me" });

    expect(httpStatusOf(result.error)).toBeUndefined();
  });
});
