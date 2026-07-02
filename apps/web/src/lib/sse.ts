/**
 * T-104: typed wrapper around the notification SSE stream
 * (GET /api/v1/notifications/stream, see backend/app/modules/notifications/sse.py).
 *
 * Not a real `EventSource` -- that API can't attach an `Authorization`
 * header, and the stream endpoint only accepts a bearer JWT (no query-param
 * fallback, unlike some SSE backends). `fetch` + a manually parsed
 * `ReadableStream` gets the same one-way event semantics with the header
 * this app's access-token model requires.
 *
 * Singleton by design: every `subscribeToNotificationStream` caller (the
 * `useNotifications` hook, a toast listener, ...) shares one underlying
 * connection via ref-counted listener sets, so mounting the bell and the
 * settings page at once never opens two streams for the same tab.
 */
import { getAccessToken, getApiBaseUrl } from "@forge/api-client";
import type { NotificationPublic } from "@forge/api-client";
import type { NotificationStreamHandlers } from "@forge/hooks";

const STREAM_PATH = "/api/v1/notifications/stream";
const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 30000;
const EVENT_SEPARATOR = "\n\n";
const COMMENT_PREFIX = ":";

const notificationListeners = new Set<(notification: NotificationPublic) => void>();
const connectionListeners = new Set<(connected: boolean) => void>();

let abortController: AbortController | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
let connected = false;

function setConnected(value: boolean): void {
  if (connected === value) {
    return;
  }
  connected = value;
  for (const listener of connectionListeners) {
    listener(value);
  }
}

function isTabHidden(): boolean {
  return typeof document !== "undefined" && document.hidden;
}

interface ParsedEvent {
  event: string;
  data: string;
}

function parseEventBlock(block: string): ParsedEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(COMMENT_PREFIX)) {
      continue; // ": ping" keepalive -- nothing to parse
    }
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  return dataLines.length > 0 ? { event, data: dataLines.join("\n") } : null;
}

async function readStream(signal: AbortSignal): Promise<void> {
  const token = getAccessToken();
  if (!token) {
    throw new Error("No access token available for the notification stream");
  }
  const response = await fetch(`${getApiBaseUrl()}${STREAM_PATH}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Notification stream request failed with status ${String(response.status)}`);
  }

  setConnected(true);
  reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        return;
      }
      buffer += decoder.decode(value, { stream: true });
      let separatorIndex = buffer.indexOf(EVENT_SEPARATOR);
      while (separatorIndex !== -1) {
        const block = buffer.slice(0, separatorIndex);
        buffer = buffer.slice(separatorIndex + EVENT_SEPARATOR.length);
        const parsed = parseEventBlock(block);
        if (parsed?.event === "notification") {
          const notification = JSON.parse(parsed.data) as NotificationPublic;
          for (const listener of notificationListeners) {
            listener(notification);
          }
        }
        separatorIndex = buffer.indexOf(EVENT_SEPARATOR);
      }
    }
  } finally {
    setConnected(false);
  }
}

function scheduleReconnect(): void {
  if (reconnectTimer || isTabHidden() || notificationListeners.size === 0) {
    return;
  }
  const delay = reconnectDelayMs;
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connectLoop();
  }, delay);
}

function connectLoop(): Promise<void> {
  if (abortController || isTabHidden() || notificationListeners.size === 0) {
    return Promise.resolve();
  }
  const controller = new AbortController();
  abortController = controller;
  return readStream(controller.signal)
    .catch(() => {
      // Any failure mode (network drop, 401, server restart, deliberate
      // abort below) is handled uniformly by the reconnect scheduling in
      // `finally` -- nothing case-specific to do here.
    })
    .finally(() => {
      abortController = null;
      scheduleReconnect();
    });
}

function disconnect(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  abortController?.abort();
  abortController = null;
  setConnected(false);
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      disconnect();
    } else if (notificationListeners.size > 0) {
      reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
      void connectLoop();
    }
  });
}

export function subscribeToNotificationStream(handlers: NotificationStreamHandlers): () => void {
  const { onNotification, onConnectedChange } = handlers;
  notificationListeners.add(onNotification);
  connectionListeners.add(onConnectedChange);
  onConnectedChange(connected);

  if (!isTabHidden()) {
    void connectLoop();
  }

  return () => {
    notificationListeners.delete(onNotification);
    connectionListeners.delete(onConnectedChange);
    if (notificationListeners.size === 0) {
      disconnect();
      reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
    }
  };
}
