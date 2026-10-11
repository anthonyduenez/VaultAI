import { describe, expect, it } from "vitest";
import { bboxToStyle, insetsToCrop } from "./geometry";

describe("bboxToStyle", () => {
  it("turns a normalized box into percentage offsets", () => {
    expect(bboxToStyle([0.1, 0.2, 0.5, 0.4])).toEqual({ left: "10%", top: "20%", width: "40%", height: "20%" });
  });

  it("normalizes swapped corners and clamps to the page", () => {
    expect(bboxToStyle([0.5, 0.4, 0.1, 0.2])).toEqual({ left: "10%", top: "20%", width: "40%", height: "20%" });
    expect(bboxToStyle([-0.1, 0, 1.2, 0.5])).toEqual({ left: "0%", top: "0%", width: "100%", height: "50%" });
  });
});

describe("insetsToCrop", () => {
  it("converts edge insets to a crop box, or null when nothing is cropped", () => {
    expect(insetsToCrop({ left: 0.1, top: 0, right: 0.25, bottom: 0.5 })).toEqual([0.1, 0, 0.75, 0.5]);
    expect(insetsToCrop({ left: 0, top: 0, right: 0, bottom: 0 })).toBeNull();
  });
});
