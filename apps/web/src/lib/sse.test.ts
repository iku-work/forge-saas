import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "@forge/api-client";

import { subscribeToNotificationStream } from "./sse";

/**
 * The stream is a singleton with a ref-counted listener set -- "mounting the
 * bell and the settings page at once never opens two streams" (see sse.ts).
 * That guarantee is only as good as the bookkeeping around `abortController`,
 * and an aborted fetch does not settle synchronously: hiding and re-showing a
 * tab quickly starts the replacement connection before the abandoned one's
 * `finally` runs. That `finally` used to null out the registration
 * unconditionally, so the replacement became invisible to the singleton check
 * and the scheduled reconnect opened a second live stream on top of it --
 * every notification then arriving twice, and the count doubling on each
 * repeat.
 */
describe("notification stream (tab hidden and shown again)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let hidden = false;

  beforeEach(() => {
    vi.useFakeTimers();
    setAccessToken("test-token");
    hidden = false;
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => hidden,
    });
    // A body that never completes, so each connection stays "live" until it
    // is aborted -- exactly like the real endpoint between events.
    fetchMock = vi.fn((_url: string, init?: { signal?: AbortSignal }) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          // No chunks: the connection is open and idle, like the real
          // endpoint between events. Aborting errors the *body*, which is
          // how a real fetch surfaces it once the response has resolved --
          // the pending reader.read() rejects, and that is what makes
          // readStream unwind at all.
          init?.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("Aborted", "AbortError"));
          });
        },
      });
      return Promise.resolve(new Response(body, { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("keeps exactly one connection when the tab is hidden and shown again", async () => {
    const unsubscribe = subscribeToNotificationStream({
      onNotification: () => undefined,
      onConnectedChange: () => undefined,
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));

    // The replacement connection is open; the abandoned one's rejection is
    // still queued behind it.
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // Past every reconnect backoff the abandoned stream could have scheduled.
    await vi.advanceTimersByTimeAsync(35_000);

    expect(fetchMock).toHaveBeenCalledTimes(2);

    unsubscribe();
  });
});
