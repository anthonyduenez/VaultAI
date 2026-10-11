import type { Tone } from "./display";

const LOCALE = "en-US";

const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string): Intl.NumberFormat | null {
  const cached = moneyFormatters.get(currency);
  if (cached) return cached;
  try {
    const formatter = new Intl.NumberFormat(LOCALE, { style: "currency", currency });
    moneyFormatters.set(currency, formatter);
    return formatter;
  } catch {
    return null;
  }
}

const plainAmount = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatMoney(amount: string | null, currency: string | null): string {
  if (amount === null || amount.trim() === "") return "—";
  const value = Number(amount);
  if (!Number.isFinite(value)) return amount;
  const formatter = currency ? moneyFormatter(currency.toUpperCase()) : null;
  if (formatter) return formatter.format(value);
  return currency ? `${plainAmount.format(value)} ${currency}` : plainAmount.format(value);
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function parseIsoDate(value: string): { y: number; m: number; d: number } | null {
  const match = ISO_DATE.exec(value);
  if (!match) return null;
  const [, y, m, d] = match;
  return { y: Number(y), m: Number(m), d: Number(d) };
}

const dateFormatter = new Intl.DateTimeFormat(LOCALE, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

/** "2026-03-04" → "Mar 4, 2026". Calendar dates carry no time zone, so none is applied. */
export function formatDate(value: string | null): string {
  if (!value) return "—";
  const parts = parseIsoDate(value);
  if (!parts) return value;
  return dateFormatter.format(new Date(Date.UTC(parts.y, parts.m - 1, parts.d)));
}

const dateTimeFormatter = new Intl.DateTimeFormat(LOCALE, {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateTimeFormatter.format(date);
}

export function isoDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function daysBetween(from: string, to: string): number | null {
  const a = parseIsoDate(from);
  const b = parseIsoDate(to);
  if (!a || !b) return null;
  return Math.round((Date.UTC(b.y, b.m - 1, b.d) - Date.UTC(a.y, a.m - 1, a.d)) / 86_400_000);
}

interface DueLabel {
  text: string;
  tone: Tone;
}

export const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;

/** Trimmed form input for an optional API field: blank becomes null. */
export function optionalText(text: string): string | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : trimmed;
}

export function relativeDue(due: string, today: string): DueLabel {
  const days = daysBetween(today, due);
  if (days === null) return { text: due, tone: "neutral" };
  if (days < 0) return { text: `overdue by ${plural(-days, "day")}`, tone: "danger" };
  if (days === 0) return { text: "due today", tone: "danger" };
  if (days === 1) return { text: "due tomorrow", tone: "warning" };
  return { text: `in ${plural(days, "day")}`, tone: days <= 14 ? "warning" : "neutral" };
}

export function relativeTime(iso: string, now: Date): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((now.getTime() - then) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${plural(minutes, "min")} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${plural(hours, "hour")} ago`;
  const days = Math.round(hours / 24);
  return days < 30 ? `${plural(days, "day")} ago` : formatDateTime(iso);
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatBytes(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${BYTE_UNITS[unit]}`;
}

export function formatPercent(ratio: number | null, digits = 0): string {
  if (ratio === null || !Number.isFinite(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function formatMs(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

export function formatCount(n: number): string {
  return n.toLocaleString(LOCALE);
}

export function shortHash(sha256: string): string {
  return sha256.slice(0, 12);
}

export function shortModelName(name: string): string {
  return name.split("/").pop() || name;
}
