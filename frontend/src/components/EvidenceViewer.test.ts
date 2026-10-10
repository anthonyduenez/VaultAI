import { describe, expect, it } from "vitest";
import type { PageInfo } from "../api/types";
import { locateEvidence } from "./EvidenceViewer";

const page = (page_no: number, lines: [string, [number, number, number, number]][]): PageInfo => ({
  page_no,
  width: 612,
  height: 792,
  text_method: "pdf_text",
  ocr_confidence: null,
  has_image: true,
  lines: lines.map(([id, bbox]) => ({ id, text: `line ${id}`, bbox, confidence: null, flag: null })),
});

describe("locateEvidence", () => {
  const pages = [
    page(1, [["a", [0.1, 0.1, 0.9, 0.15]]]),
    page(2, [["c", [0.1, 0.1, 0.9, 0.15]], ["d", [0.1, 0.3, 0.6, 0.35]]]),
  ];

  it("uses the evidence page and box when both are given", () => {
    expect(locateEvidence(pages, { page: 2, bbox: [0.2, 0.3, 0.4, 0.5], line_id: null, text: null })).toEqual({
      pageNo: 2,
      bbox: [0.2, 0.3, 0.4, 0.5],
      lineId: null,
    });
  });

  it("finds the page and box from a line id alone", () => {
    expect(locateEvidence(pages, { page: null, bbox: null, line_id: "d", text: null })).toEqual({
      pageNo: 2,
      bbox: [0.1, 0.3, 0.6, 0.35],
      lineId: "d",
    });
  });

  it("returns null when the evidence points nowhere", () => {
    expect(locateEvidence(pages, { page: 9, bbox: null, line_id: null, text: "x" })).toBeNull();
    expect(locateEvidence(pages, { page: 1, bbox: null, line_id: null, text: "x" })).toEqual({ pageNo: 1, bbox: null, lineId: null });
  });
});
