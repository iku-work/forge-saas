import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatDate(iso: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(iso));
}

// Largest-to-smallest so a duration gets divided down through each unit
// until it fits inside one -- e.g. 9000s -> divided by 60 (minutes) twice
// lands under the "hours" threshold, formatting as "2 hours ago" instead of
// "150 minutes ago".
const RELATIVE_TIME_DIVISIONS: ReadonlyArray<{
  amount: number;
  unit: Intl.RelativeTimeFormatUnit;
}> = [
  { amount: 60, unit: "seconds" },
  { amount: 60, unit: "minutes" },
  { amount: 24, unit: "hours" },
  { amount: 7, unit: "days" },
  { amount: 4.34524, unit: "weeks" },
  { amount: 12, unit: "months" },
  { amount: Number.POSITIVE_INFINITY, unit: "years" },
];

// Plain-English fallback for runtimes with no Intl.RelativeTimeFormat --
// notably Expo Go's bundled Hermes, which (unlike a production build's
// Hermes, configurable for full ICU data) throws "Cannot read property
// 'prototype' of undefined" on `new Intl.RelativeTimeFormat(...)`. Every
// caller of this shared (web + mobile) util needs to survive that rather
// than crash the screen -- the web app always has full Intl support, so
// this path is Hermes-only in practice.
function formatRelativeTimeFallback(value: number, unit: Intl.RelativeTimeFormatUnit): string {
  const n = Math.abs(value);
  const noun = n === 1 ? unit.replace(/s$/, "") : unit;
  if (value === 0) return "now";
  return value < 0 ? `${n} ${noun} ago` : `in ${n} ${noun}`;
}

export function formatRelativeTime(iso: string, locale = "en-US"): string {
  let formatter: Intl.RelativeTimeFormat | null;
  try {
    formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  } catch {
    formatter = null;
  }
  let duration = (new Date(iso).getTime() - Date.now()) / 1000;
  for (const division of RELATIVE_TIME_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      const rounded = Math.round(duration);
      return formatter
        ? formatter.format(rounded, division.unit)
        : formatRelativeTimeFallback(rounded, division.unit);
    }
    duration /= division.amount;
  }
  const rounded = Math.round(duration);
  return formatter
    ? formatter.format(rounded, "years")
    : formatRelativeTimeFallback(rounded, "years");
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), BYTE_UNITS.length - 1);
  const value = bytes / 1024 ** exponent;
  const unit = BYTE_UNITS[exponent] ?? "B";
  return `${exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${unit}`;
}

// Unicode combining diacritical marks span code points 0x300-0x36f. Stripping
// them after NFKD normalization turns accented input ("Café") into ascii
// ("cafe") instead of having the accent dropped as an unrecognized symbol by
// the [^a-z0-9]+ collapse below.
const COMBINING_MARK_START = 0x300;
const COMBINING_MARK_END = 0x36f;

function stripCombiningMarks(input: string): string {
  let result = "";
  for (const char of input) {
    const code = char.codePointAt(0) ?? 0;
    if (code >= COMBINING_MARK_START && code <= COMBINING_MARK_END) continue;
    result += char;
  }
  return result;
}

export function slugify(str: string): string {
  return stripCombiningMarks(str.normalize("NFKD"))
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// T-262: the shape supportMailtoUrl below needs, declared structurally so
// this package doesn't have to depend on @forge/hooks (whose SupportContext
// satisfies it) or on the generated API client -- apps/mobile maps its own
// response to the same field names and passes it straight in.
export interface SupportMailtoContext {
  userId: string;
  email: string;
  orgId: string;
  orgName: string;
  plan: string | null;
  subscriptionStatus: string | null;
  locale: string;
}

// Deliberately not translated: this block is read by the support agent
// receiving the mail, not by the user sending it -- same reasoning as a bug
// report's diagnostics footer. What the *user* reads (the "Contact support"
// label, the subject line passed in below) does go through i18n.
function contextBlock(ctx: SupportMailtoContext): string {
  return [
    "",
    "",
    "---",
    "Please keep the details below — they help us find your account.",
    `User: ${ctx.email} (${ctx.userId})`,
    `Organization: ${ctx.orgName} (${ctx.orgId})`,
    `Plan: ${ctx.plan ?? "none"} (${ctx.subscriptionStatus ?? "no subscription"})`,
    `Locale: ${ctx.locale}`,
  ].join("\n");
}

/**
 * Pre-populates a `mailto:` support link with the user's context, so the
 * first reply doesn't have to start with "which account are you on?".
 *
 * Any other URL (a help centre, a hosted contact form) is returned
 * unchanged: there is no portable way to prefill one, and appending query
 * params to somebody's ticket form would at best be ignored and at worst
 * break it. Existing params on the mailto: (a `cc`, a fixed `subject`) are
 * preserved.
 */
export function supportMailtoUrl(
  url: string,
  ctx: SupportMailtoContext | null,
  options: { subject?: string } = {},
): string {
  if (!url.startsWith("mailto:") || ctx === null) {
    return url;
  }
  const [address, query = ""] = url.slice("mailto:".length).split("?", 2);
  const params = new URLSearchParams(query);
  if (options.subject !== undefined && !params.has("subject")) {
    params.set("subject", options.subject);
  }
  params.set("body", `${params.get("body") ?? ""}${contextBlock(ctx)}`);
  // URLSearchParams encodes spaces as "+", which mail clients render
  // literally in a body; mailto: takes percent-encoding (RFC 6068 §2).
  return `mailto:${address}?${params.toString().replace(/\+/g, "%20")}`;
}
