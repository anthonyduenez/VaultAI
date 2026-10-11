import { describe, expect, it } from "vitest";
import { formatBytes, formatDate, formatMoney, formatPercent, formatReasonCounts, relativeDue } from "./format";

describe("formatMoney", () => {
  it("formats decimal strings with their currency", () => {
    expect(formatMoney("5328.00", "USD")).toBe("$5,328.00");
    expect(formatMoney("1234.5", "EUR")).toBe("€1,234.50");
    expect(formatMoney("-42", "usd")).toBe("-$42.00");
  });

  it("falls back to a plain amount when the currency is missing or invalid", () => {
    expect(formatMoney("12", null)).toBe("12.00");
    expect(formatMoney("99.1", "US")).toBe("99.10 US");
  });

  it("shows a dash for no amount and keeps unparseable text as-is", () => {
    expect(formatMoney(null, "USD")).toBe("—");
    expect(formatMoney("about 5k", "USD")).toBe("about 5k");
  });
});

describe("formatDate", () => {
  it("formats ISO calendar dates without shifting the day", () => {
    expect(formatDate("2026-03-04")).toBe("Mar 4, 2026");
    expect(formatDate("2026-12-31")).toBe("Dec 31, 2026");
  });

  it("returns non-ISO input unchanged and a dash for null", () => {
    expect(formatDate("03/04/2026")).toBe("03/04/2026");
    expect(formatDate(null)).toBe("—");
  });
});

describe("relativeDue", () => {
  const today = "2026-10-10";

  it("describes future and past due dates", () => {
    expect(relativeDue("2026-11-01", today)).toEqual({ text: "in 22 days", tone: "neutral" });
    expect(relativeDue("2026-10-20", today)).toEqual({ text: "in 10 days", tone: "warning" });
    expect(relativeDue("2026-10-07", today)).toEqual({ text: "overdue by 3 days", tone: "danger" });
    expect(relativeDue("2026-10-09", today)).toEqual({ text: "overdue by 1 day", tone: "danger" });
  });

  it("names today and tomorrow", () => {
    expect(relativeDue("2026-10-10", today)).toEqual({ text: "due today", tone: "danger" });
    expect(relativeDue("2026-10-11", today)).toEqual({ text: "due tomorrow", tone: "warning" });
  });

  it("counts calendar days across a daylight-saving change", () => {
    expect(relativeDue("2026-11-05", "2026-10-30").text).toBe("in 6 days");
  });
});

describe("formatBytes and formatPercent", () => {
  it("formats sizes and ratios", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatPercent(0.934)).toBe("93%");
    expect(formatPercent(null)).toBe("—");
  });
});

describe("formatReasonCounts", () => {
  it("lists only non-zero codes, most frequent first", () => {
    expect(formatReasonCounts({ empty_content: 1, invalid_json: 0, schema_invalid: 3 })).toBe("schema invalid 3 · empty content 1");
  });
  it("shows a dash when nothing was refused or the field is absent", () => {
    expect(formatReasonCounts({ empty_content: 0 })).toBe("—");
    expect(formatReasonCounts(undefined)).toBe("—");
  });
});
