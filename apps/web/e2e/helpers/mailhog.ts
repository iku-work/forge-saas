// Polls MailHog's own API (never the SMTP port) for a link matching a given
// pattern in whatever the backend just enqueued to a recipient's mailbox,
// then pulls it straight out of the rendered HTML body. MailHog keeps every
// message it has ever received for the life of the container and has no
// per-test isolation, so this always matches by *link pattern*, not by "the
// newest message" or "the first message" -- a single mailbox (e.g. the
// invitee's address, which receives both an invitation and later its own
// verification email) can hold several messages by the time this runs, in
// an order this helper never assumes.
import { MAILHOG_URL } from "../config";

interface MailhogHeaders {
  [name: string]: string[] | undefined;
}

interface MailhogPart {
  Headers: MailhogHeaders;
  Body: string;
  MIME?: { Parts?: MailhogPart[] } | null;
}

interface MailhogItem {
  ID: string;
  Content: MailhogPart;
  // Confirmed empirically against a real MailHog v2 response: a
  // multipart message's sub-parts live in MIME.Parts on the *item* itself,
  // not nested under Content.MIME (Content.MIME is absent/undefined even
  // when the message has multiple parts) -- Content is just the outer
  // envelope (its own Body is the raw, un-decoded multipart blob).
  MIME?: { Parts?: MailhogPart[] } | null;
}

interface MailhogSearchResponse {
  total: number;
  count: number;
  items: MailhogItem[];
}

function headerValue(headers: MailhogHeaders, name: string): string {
  return headers[name]?.[0] ?? "";
}

// Decodes RFC 2045 quoted-printable: "=\r\n" / "=\n" soft line breaks
// disappear, "=XX" becomes the byte 0xXX. EmailMessage.add_alternative()
// (backend/app/workers/jobs/email.py) picks this encoding for any part with
// non-ASCII bytes, and MailHog's API always returns the encoded wire form,
// never a decoded one.
function decodeQuotedPrintable(input: string): string {
  const withoutSoftBreaks = input.replace(/=\r\n|=\n/g, "");
  const bytes = withoutSoftBreaks.replace(/=([0-9A-Fa-f]{2})/g, (_match, hex: string) =>
    String.fromCharCode(parseInt(hex, 16)),
  );
  return Buffer.from(bytes, "binary").toString("utf-8");
}

function decodeBody(part: MailhogPart): string {
  const encoding = headerValue(part.Headers, "Content-Transfer-Encoding").toLowerCase();
  if (encoding === "quoted-printable") {
    return decodeQuotedPrintable(part.Body);
  }
  if (encoding === "base64") {
    return Buffer.from(part.Body.replace(/\s+/g, ""), "base64").toString("utf-8");
  }
  return part.Body;
}

// Depth-first search through a (possibly multipart/alternative, possibly
// nested) message for every part whose Content-Type is text/html, falling
// back to the top-level Content itself for a plain, non-multipart message
// (which has no item.MIME.Parts at all).
function htmlParts(item: MailhogItem): MailhogPart[] {
  const found: MailhogPart[] = [];
  const visit = (part: MailhogPart): void => {
    if (headerValue(part.Headers, "Content-Type").toLowerCase().includes("text/html")) {
      found.push(part);
    }
    for (const child of part.MIME?.Parts ?? []) {
      visit(child);
    }
  };
  const parts = item.MIME?.Parts ?? [];
  if (parts.length === 0) {
    return [item.Content];
  }
  for (const part of parts) {
    visit(part);
  }
  return found;
}

// The rendered templates (packages/email-templates/src/templates/*.tsx) put
// the action link in an href, HTML-entity-encoded ("&amp;" for a literal
// "&"), hence the unescape on the way out.
function extractLink(html: string, pattern: RegExp): string | null {
  const match = pattern.exec(html);
  return match ? match[0].replace(/&amp;/g, "&") : null;
}

// Polls until some message addressed to `to` contains a link matching
// `pattern`, then returns it. Scans *all* messages currently in the
// mailbox on every poll (not just the newest), so it doesn't matter which
// arrives first when a mailbox receives more than one email during a run
// (e.g. an invitee's own invitation + later verification email).
export async function waitForLink(
  to: string,
  pattern: RegExp,
  { timeoutMs = 20_000, intervalMs = 500 }: { timeoutMs?: number; intervalMs?: number } = {},
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const response = await fetch(
      `${MAILHOG_URL}/api/v2/search?kind=to&query=${encodeURIComponent(to)}&limit=20`,
    );
    if (!response.ok) {
      throw new Error(`MailHog search failed: ${String(response.status)} ${await response.text()}`);
    }
    const body = (await response.json()) as MailhogSearchResponse;
    for (const item of body.items) {
      for (const part of htmlParts(item)) {
        const link = extractLink(decodeBody(part), pattern);
        if (link) {
          return link;
        }
      }
    }
    if (Date.now() > deadline) {
      throw new Error(
        `No email to ${to} with a link matching ${pattern.toString()} arrived within ${String(timeoutMs)}ms (mailbox has ${String(body.count)} message(s))`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
