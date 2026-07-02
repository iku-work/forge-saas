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

export function formatRelativeTime(iso: string, locale = "en-US"): string {
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  let duration = (new Date(iso).getTime() - Date.now()) / 1000;
  for (const division of RELATIVE_TIME_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return formatter.format(Math.round(duration), division.unit);
    }
    duration /= division.amount;
  }
  return formatter.format(Math.round(duration), "years");
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
